"""
Vector store - ChromaDB for email storage and retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

import config

logger = logging.getLogger(__name__)


class EmailVectorStore:
    """ChromaDB-based email storage with semantic search."""
    
    def __init__(self):
        logger.info(f"Loading embedding model: {config.EMBEDDING_MODEL}")
        self._embedder = SentenceTransformer(config.EMBEDDING_MODEL)
        
        logger.info(f"Initializing ChromaDB at: {config.DB_PATH}")
        self._client = chromadb.PersistentClient(
            path=str(config.DB_PATH),
            settings=Settings(anonymized_telemetry=False)
        )
        self._collection = self._client.get_or_create_collection(
            name="emails",
            metadata={"hnsw:space": "cosine"}
        )
        try:
            logger.info(f"Collection has {self._collection.count()} documents")
        except Exception as e:
            logger.warning(f"Could not get collection count on startup: {e}")

    def _embed(self, texts: List[str]) -> List[List[float]]:
        return self._embedder.encode(texts, convert_to_numpy=True).tolist()
        
    def add_emails(self, emails: List[Any], batch_size: int = 100) -> int:
        """Add emails to the store. Returns count added."""
        added = 0
        
        for i in range(0, len(emails), batch_size):
            batch = emails[i:i + batch_size]
            
            ids = []
            documents = []
            metadatas = []
            
            for email in batch:
                doc_id = email.unique_id
                
                # Skip duplicates
                existing = self._collection.get(ids=[doc_id])
                if existing['ids']:
                    continue
                    
                ids.append(doc_id)
                documents.append(email.to_document())
                metadatas.append(email.to_metadata())
            
            if not ids:
                continue
                
            embeddings = self._embed(documents)
            
            self._collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas
            )
            added += len(ids)
            
        return added
        
    def search(self,
               query: str,
               n_results: int = 10,
               where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Semantic search for emails."""
        
        query_embedding = self._embed([query])[0]
        
        # First try with filters
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        # If filters returned nothing, try without filters
        if not results['ids'][0] and where is not None:
            logger.warning(f"No results with filter {where}, retrying without filter")
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"]
            )
        
        formatted = []
        for i in range(len(results['ids'][0])):
            formatted.append({
                'id': results['ids'][0][i],
                'document': results['documents'][0][i],
                'metadata': results['metadatas'][0][i],
                'distance': results['distances'][0][i],
                'relevance': 1 - results['distances'][0][i]
            })
            
        logger.info(f"Search '{query[:30]}...' returned {len(formatted)} results")
        return formatted
    
    def get_thread_emails(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Get all emails belonging to a conversation thread."""
        if not conversation_id:
            return []

        try:
            results = self._collection.get(
                where={"conversation_id": conversation_id},
                include=["documents", "metadatas"]
            )
        except Exception as e:
            logger.warning(f"Thread lookup failed for {conversation_id}: {e}")
            return []

        emails = []
        for i in range(len(results['ids'])):
            emails.append({
                'id': results['ids'][i],
                'document': results['documents'][i] if results['documents'] else '',
                'metadata': results['metadatas'][i] if results['metadatas'] else {}
            })

        # Sort by date within thread
        emails.sort(key=lambda e: e['metadata'].get('date', ''))
        return emails

    def get_open_items(self) -> List[Dict[str, Any]]:
        """Get unreplied inbound emails grouped by conversation thread."""
        try:
            results = self._collection.get(
                where={"$and": [{"direction": "received"}, {"is_replied": False}]},
                include=["documents", "metadatas"],
                limit=200
            )
        except Exception as e:
            logger.warning(f"Open items query failed: {e}")
            return []

        # Group by conversation_id
        threads = {}
        standalone = []
        for i in range(len(results['ids'])):
            meta = results['metadatas'][i] if results['metadatas'] else {}
            email = {
                'id': results['ids'][i],
                'document': results['documents'][i] if results['documents'] else '',
                'metadata': meta
            }
            conv_id = meta.get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(email)
            else:
                standalone.append(email)

        # Build thread summaries
        open_items = []
        for conv_id, emails in threads.items():
            # Get full thread context
            full_thread = self.get_thread_emails(conv_id)
            if not full_thread:
                full_thread = emails

            latest = max(full_thread, key=lambda e: e['metadata'].get('date', ''))
            latest_meta = latest['metadata']

            # Determine thread status
            last_direction = latest_meta.get('direction', 'received')
            last_replied = latest_meta.get('is_replied', False)

            if last_direction == 'sent' and not last_replied:
                status = 'awaiting_response'
            elif last_direction == 'received' and not last_replied:
                status = 'needs_action'
            else:
                status = 'completed'

            open_items.append({
                'conversation_id': conv_id,
                'subject': latest_meta.get('subject', ''),
                'sender': latest_meta.get('sender', ''),
                'sender_name': latest_meta.get('sender_name', ''),
                'date': latest_meta.get('date', ''),
                'message_count': len(full_thread),
                'status': status,
                'participants': list(set(
                    e['metadata'].get('sender', '') for e in full_thread if e['metadata'].get('sender')
                ))
            })

        # Add standalone emails (no conversation_id)
        for email in standalone:
            meta = email['metadata']
            open_items.append({
                'conversation_id': '',
                'subject': meta.get('subject', ''),
                'sender': meta.get('sender', ''),
                'sender_name': meta.get('sender_name', ''),
                'date': meta.get('date', ''),
                'message_count': 1,
                'status': 'needs_action',
                'participants': [meta.get('sender', '')]
            })

        # Sort by date descending (newest first)
        open_items.sort(key=lambda x: x['date'], reverse=True)
        return open_items

    def debug_sample(self, n: int = 5) -> List[Dict[str, Any]]:
        """Get sample emails for debugging."""
        results = self._collection.get(
            limit=n,
            include=["documents", "metadatas"]
        )
        
        samples = []
        for i in range(len(results['ids'])):
            samples.append({
                'id': results['ids'][i],
                'document': results['documents'][i][:500] if results['documents'] else '',
                'metadata': results['metadatas'][i] if results['metadatas'] else {}
            })
        return samples
        
    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics."""
        total = self._collection.count()
        
        sample = self._collection.get(limit=min(1000, total), include=["metadatas"])
        
        stats = {
            "total_emails": total,
            "sent": 0,
            "received": 0,
            "unread": 0,
            "flagged": 0,
        }
        
        for meta in sample['metadatas']:
            if meta.get('direction') == 'sent':
                stats['sent'] += 1
            else:
                stats['received'] += 1
            if not meta.get('is_read'):
                stats['unread'] += 1
            if meta.get('is_flagged'):
                stats['flagged'] += 1
                
        return stats
        
    def clear(self):
        """Clear all emails."""
        self._client.delete_collection("emails")
        self._collection = self._client.create_collection(
            name="emails",
            metadata={"hnsw:space": "cosine"}
        )


_store: Optional[EmailVectorStore] = None

def get_vector_store() -> EmailVectorStore:
    global _store
    if _store is None:
        _store = EmailVectorStore()
    return _store
