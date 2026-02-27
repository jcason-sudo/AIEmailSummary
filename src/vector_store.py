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
        """Get open email threads by analyzing the last message in each conversation.

        Thread status is determined by WHO sent the last message:
        - Last message is RECEIVED + not replied → needs_action (you owe a reply)
        - Last message is SENT → awaiting_response (they owe you a reply)
        - Last message is RECEIVED + replied → completed

        This does NOT rely on is_replied for sent emails (which is always False
        since you don't reply to your own sent mail). Instead it looks at the
        actual conversation timeline.
        """
        # Get ALL emails to build complete thread picture
        try:
            total = self._collection.count()
            if total == 0:
                return []
            results = self._collection.get(
                include=["metadatas"],
                limit=min(total, 5000)
            )
        except Exception as e:
            logger.warning(f"Open items query failed: {e}")
            return []

        # Group by conversation_id
        threads = {}  # conv_id -> list of metadata dicts
        standalone = []
        for i in range(len(results['ids'])):
            meta = results['metadatas'][i] if results['metadatas'] else {}
            meta['_id'] = results['ids'][i]
            conv_id = meta.get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(meta)
            else:
                # Standalone received + not replied = needs action
                if meta.get('direction') == 'received' and not meta.get('is_replied'):
                    standalone.append(meta)

        # Analyze each thread
        open_items = []
        for conv_id, messages in threads.items():
            # Sort by date to find the last message
            messages.sort(key=lambda m: m.get('date', ''))
            latest = messages[-1]

            last_direction = latest.get('direction', 'received')
            last_replied = latest.get('is_replied', False)

            # Determine thread status based on who sent the last message
            if last_direction == 'received' and not last_replied:
                status = 'needs_action'
            elif last_direction == 'sent':
                # I sent the last message — check if anyone replied after
                # (if my sent is the latest, they haven't responded)
                status = 'awaiting_response'
            elif last_direction == 'received' and last_replied:
                status = 'completed'
            else:
                status = 'completed'

            # Skip completed threads
            if status == 'completed':
                continue

            open_items.append({
                'conversation_id': conv_id,
                'subject': latest.get('subject', ''),
                'sender': latest.get('sender', ''),
                'sender_name': latest.get('sender_name', ''),
                'date': latest.get('date', ''),
                'message_count': len(messages),
                'status': status,
                'participants': list(set(
                    m.get('sender', '') for m in messages if m.get('sender')
                ))
            })

        # Add standalone emails (no conversation_id, received, not replied)
        for meta in standalone:
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
