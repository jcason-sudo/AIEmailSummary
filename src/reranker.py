"""
Cross-encoder reranker for search results.

Takes top-N candidates from hybrid search and reranks them using a
cross-encoder model that scores (query, document) pairs directly.
Much more accurate than bi-encoder similarity but too slow for full corpus.
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class Reranker:
    """Cross-encoder reranker using sentence-transformers."""

    def __init__(self):
        self._model = None

    def _ensure_model(self):
        """Lazy-load the cross-encoder model."""
        if self._model is None:
            logger.info(f"Loading cross-encoder model: {RERANKER_MODEL}")
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(RERANKER_MODEL)
            logger.info("Cross-encoder model loaded")

    def rerank(self,
               query: str,
               candidates: List[Dict[str, Any]],
               top_k: int = 15) -> List[Dict[str, Any]]:
        """Rerank candidates using the cross-encoder.

        Args:
            query: The search query.
            candidates: List of email result dicts (must have 'document' key).
            top_k: Number of results to return.

        Returns:
            Top-k candidates sorted by cross-encoder score, with 'rerank_score' added.
        """
        if not candidates:
            return []

        if len(candidates) <= top_k:
            return candidates

        self._ensure_model()

        # Build (query, document) pairs for scoring
        pairs = []
        for c in candidates:
            doc = c.get('document', '')
            # Truncate long documents for cross-encoder (max ~512 tokens)
            if len(doc) > 1500:
                doc = doc[:1500]
            pairs.append((query, doc))

        # Score all pairs
        scores = self._model.predict(pairs)

        # Attach scores and sort
        for i, c in enumerate(candidates):
            c['rerank_score'] = float(scores[i])

        candidates.sort(key=lambda x: x['rerank_score'], reverse=True)

        logger.info(
            f"Reranked {len(candidates)} candidates → top {top_k} "
            f"(best: {candidates[0]['rerank_score']:.3f}, "
            f"worst kept: {candidates[min(top_k-1, len(candidates)-1)]['rerank_score']:.3f})"
        )

        return candidates[:top_k]


# Module-level singleton
_reranker: Optional[Reranker] = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
