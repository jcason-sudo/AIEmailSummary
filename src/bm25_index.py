"""
BM25 lexical search index for emails.

Complements ChromaDB's semantic search by finding exact keyword matches
that embedding models miss (e.g., PO numbers, project codes, names).
"""

import logging
import pickle
import re
from pathlib import Path
from typing import List, Tuple, Optional

from rank_bm25 import BM25Okapi

import config

logger = logging.getLogger(__name__)

INDEX_PATH = config.DB_PATH / "bm25_index.pkl"


def _tokenize(text: str) -> List[str]:
    """Simple whitespace tokenizer with lowercase and punctuation stripping."""
    text = text.lower()
    # Split on whitespace and strip surrounding punctuation
    tokens = re.findall(r'\b\w[\w\-.]*\w\b|\b\w\b', text)
    return tokens


class BM25Index:
    """BM25 lexical search index backed by rank_bm25."""

    def __init__(self):
        self._doc_ids: List[str] = []
        self._corpus: List[List[str]] = []
        self._bm25: Optional[BM25Okapi] = None

    @property
    def size(self) -> int:
        return len(self._doc_ids)

    def build_from_chromadb(self, collection) -> int:
        """Build index from all documents in a ChromaDB collection."""
        total = collection.count()
        if total == 0:
            logger.info("BM25: No documents to index")
            return 0

        logger.info(f"BM25: Building index from {total} documents...")

        results = collection.get(
            include=["documents"],
            limit=min(total, 10000)
        )

        self._doc_ids = list(results['ids'])
        self._corpus = [_tokenize(doc) for doc in results['documents']]
        self._bm25 = BM25Okapi(self._corpus)

        logger.info(f"BM25: Index built with {len(self._doc_ids)} documents")
        self.save()
        return len(self._doc_ids)

    def add_documents(self, doc_ids: List[str], texts: List[str]):
        """Add documents incrementally. Rebuilds BM25 (fast for <10K docs)."""
        for doc_id, text in zip(doc_ids, texts):
            if doc_id not in set(self._doc_ids):
                self._doc_ids.append(doc_id)
                self._corpus.append(_tokenize(text))

        if self._corpus:
            self._bm25 = BM25Okapi(self._corpus)
            self.save()

    def search(self, query: str, n_results: int = 50) -> List[Tuple[str, float]]:
        """Search the index. Returns list of (doc_id, score) tuples."""
        if self._bm25 is None or not self._doc_ids:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        # Get top N indices by score
        scored = [(i, scores[i]) for i in range(len(scores)) if scores[i] > 0]
        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored[:n_results]:
            results.append((self._doc_ids[idx], score))

        return results

    def save(self):
        """Persist index to disk."""
        try:
            INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'doc_ids': self._doc_ids,
                'corpus': self._corpus,
            }
            with open(INDEX_PATH, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"BM25: Saved index ({len(self._doc_ids)} docs) to {INDEX_PATH}")
        except Exception as e:
            logger.warning(f"BM25: Failed to save index: {e}")

    def load(self) -> bool:
        """Load index from disk. Returns True if successful."""
        if not INDEX_PATH.exists():
            return False

        try:
            with open(INDEX_PATH, 'rb') as f:
                data = pickle.load(f)
            self._doc_ids = data['doc_ids']
            self._corpus = data['corpus']
            if self._corpus:
                self._bm25 = BM25Okapi(self._corpus)
            logger.info(f"BM25: Loaded index ({len(self._doc_ids)} docs) from disk")
            return True
        except Exception as e:
            logger.warning(f"BM25: Failed to load index: {e}")
            return False


# Module-level singleton
_index: Optional[BM25Index] = None


def get_bm25_index() -> BM25Index:
    global _index
    if _index is None:
        _index = BM25Index()
        if not _index.load():
            logger.info("BM25: No cached index found, will build on first use")
    return _index
