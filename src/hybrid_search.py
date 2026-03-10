"""
Hybrid search combining BM25 lexical + ChromaDB semantic search
with Reciprocal Rank Fusion and cross-encoder reranking.

Pipeline: BM25 + Semantic → RRF Fusion → Cross-Encoder Rerank → Results
"""

import logging
from typing import List, Dict, Any, Optional

from vector_store import EmailVectorStore
from bm25_index import BM25Index, get_bm25_index
from reranker import Reranker, get_reranker

logger = logging.getLogger(__name__)

RRF_K = 60  # RRF constant — higher values give more weight to lower-ranked results


def reciprocal_rank_fusion(
    ranked_lists: List[List[str]],
    k: int = RRF_K
) -> List[tuple]:
    """Fuse multiple ranked lists using Reciprocal Rank Fusion.

    Args:
        ranked_lists: List of lists of doc_ids (each list is ranked best-first).
        k: RRF constant (default 60).

    Returns:
        List of (doc_id, rrf_score) tuples sorted by score descending.
    """
    scores = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused


class HybridSearch:
    """Orchestrates hybrid BM25 + semantic search with reranking."""

    def __init__(self, store: EmailVectorStore):
        self.store = store
        self.bm25 = get_bm25_index()
        self.reranker = get_reranker()
        self._ensure_bm25_built()

    def _ensure_bm25_built(self):
        """Build BM25 index if it's empty but ChromaDB has documents."""
        if self.bm25.size == 0:
            total = self.store._collection.count()
            if total > 0:
                self.bm25.build_from_chromadb(self.store._collection)

    def search(self,
               query: str,
               n_results: int = 15,
               where: Optional[Dict[str, Any]] = None,
               rerank: bool = True) -> List[Dict[str, Any]]:
        """Hybrid search with optional reranking.

        Args:
            query: Search query.
            n_results: Number of final results to return.
            where: ChromaDB metadata filter (applied to semantic branch).
            rerank: Whether to apply cross-encoder reranking.

        Returns:
            List of email result dicts matching vector_store.search() format.
        """
        # Fetch more candidates than needed for fusion/reranking
        candidate_count = min(n_results * 4, 50)

        # Branch 1: Semantic search via ChromaDB
        semantic_results = self.store.search(
            query=query,
            n_results=candidate_count,
            where=where
        )
        semantic_ids = [r['id'] for r in semantic_results]

        # Branch 2: BM25 lexical search
        bm25_hits = self.bm25.search(query, n_results=candidate_count)
        bm25_ids = [doc_id for doc_id, _ in bm25_hits]

        # If BM25 has where filters to match, post-filter
        if where and bm25_ids:
            bm25_ids = self._apply_metadata_filter(bm25_ids, where)

        # Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion([semantic_ids, bm25_ids])
        fused_ids = [doc_id for doc_id, _ in fused]

        # Build result lookup from semantic results
        result_map = {r['id']: r for r in semantic_results}

        # For BM25-only results, fetch from ChromaDB
        missing_ids = [did for did in fused_ids if did not in result_map]
        if missing_ids:
            try:
                fetched = self.store._collection.get(
                    ids=missing_ids[:50],
                    include=["documents", "metadatas"]
                )
                for i in range(len(fetched['ids'])):
                    result_map[fetched['ids'][i]] = {
                        'id': fetched['ids'][i],
                        'document': fetched['documents'][i] if fetched['documents'] else '',
                        'metadata': fetched['metadatas'][i] if fetched['metadatas'] else {},
                        'distance': 0.5,  # No semantic distance for BM25-only results
                        'relevance': 0.5,
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch BM25-only results: {e}")

        # Assemble fused results in RRF order
        candidates = []
        for doc_id, rrf_score in fused:
            if doc_id in result_map:
                result = result_map[doc_id].copy()
                result['rrf_score'] = rrf_score
                candidates.append(result)

        logger.info(
            f"Hybrid search '{query[:30]}...': "
            f"{len(semantic_results)} semantic + {len(bm25_ids)} BM25 "
            f"→ {len(candidates)} fused"
        )

        # Cross-encoder reranking
        if rerank and len(candidates) > n_results:
            candidates = self.reranker.rerank(query, candidates, top_k=n_results)
        else:
            candidates = candidates[:n_results]

        return candidates

    def _apply_metadata_filter(self, doc_ids: List[str], where: Dict) -> List[str]:
        """Post-filter BM25 results by ChromaDB metadata filter.

        Fetches metadata for the given doc_ids and filters them against
        the where clause. Only handles simple equality and $and conditions.
        """
        if not doc_ids:
            return doc_ids

        try:
            fetched = self.store._collection.get(
                ids=doc_ids[:100],
                include=["metadatas"]
            )
        except Exception:
            return doc_ids  # Can't filter, return all

        filtered = []
        for i in range(len(fetched['ids'])):
            meta = fetched['metadatas'][i] if fetched['metadatas'] else {}
            if self._matches_filter(meta, where):
                filtered.append(fetched['ids'][i])

        return filtered

    def _matches_filter(self, meta: dict, where: dict) -> bool:
        """Check if metadata matches a where filter."""
        if '$and' in where:
            return all(self._matches_filter(meta, cond) for cond in where['$and'])

        for key, value in where.items():
            if key.startswith('$'):
                continue
            if isinstance(value, dict):
                # Comparison operators
                for op, val in value.items():
                    meta_val = meta.get(key, '')
                    if op == '$gte' and meta_val < val:
                        return False
                    if op == '$lte' and meta_val > val:
                        return False
                    if op == '$eq' and meta_val != val:
                        return False
            else:
                if meta.get(key) != value:
                    return False
        return True

    def update_index(self, doc_ids: List[str], texts: List[str]):
        """Add new documents to the BM25 index after ingestion."""
        self.bm25.add_documents(doc_ids, texts)


# Module-level singleton
_hybrid: Optional['HybridSearch'] = None


def get_hybrid_search() -> 'HybridSearch':
    global _hybrid
    if _hybrid is None:
        from vector_store import get_vector_store
        _hybrid = HybridSearch(get_vector_store())
    return _hybrid
