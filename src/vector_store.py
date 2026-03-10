"""
Vector store - ChromaDB for email storage and retrieval.
"""

import logging
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

import config
from email_preprocessor import chunk_email, generate_thread_summary_chunk

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
        """Add emails to the store with smart chunking.

        Each email is segmented into chunks:
        - "fresh" chunk: newest message content (boilerplate stripped)
        - "quoted" chunk: reply history (if substantial)

        Returns count of emails processed (each may produce multiple chunks).
        """
        added = 0

        for i in range(0, len(emails), batch_size):
            batch = emails[i:i + batch_size]

            ids = []
            documents = []
            metadatas = []
            seen_ids = set()  # Track IDs within this batch to avoid duplicates

            for email in batch:
                base_id = email.unique_id

                # Skip if already seen in this batch
                fresh_id = f"{base_id}_fresh"
                if fresh_id in seen_ids:
                    continue

                # Skip if any chunk from this email already exists in DB
                try:
                    existing = self._collection.get(ids=[fresh_id])
                    if existing['ids']:
                        continue
                except Exception:
                    pass
                # Also check legacy (pre-chunking) ID for backward compat
                try:
                    existing = self._collection.get(ids=[base_id])
                    if existing['ids']:
                        continue
                except Exception:
                    pass

                # Chunk the email
                document = email.to_document()
                metadata = email.to_metadata()
                chunks = chunk_email(base_id, document, metadata)

                for chunk in chunks:
                    if chunk.chunk_id not in seen_ids:
                        ids.append(chunk.chunk_id)
                        documents.append(chunk.text)
                        metadatas.append(chunk.metadata)
                        seen_ids.add(chunk.chunk_id)

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
            logger.info(f"Batch: added {len(ids)} chunks from {len(batch)} emails")

        return added

    def add_thread_summaries(self) -> int:
        """Generate and store thread summary chunks for all conversation threads.

        Call after ingestion to create summary chunks that capture thread arcs.
        """
        total = self._collection.count()
        if total == 0:
            return 0

        results = self._collection.get(
            include=["documents", "metadatas"],
            limit=min(total, 10000)
        )

        # Group by conversation_id
        threads = {}
        for i in range(len(results['ids'])):
            meta = results['metadatas'][i] if results['metadatas'] else {}
            conv_id = meta.get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append({
                    'id': results['ids'][i],
                    'document': results['documents'][i] if results['documents'] else '',
                    'metadata': meta,
                })

        ids = []
        documents = []
        metadatas = []
        generated = 0

        for conv_id, thread_emails in threads.items():
            if len(thread_emails) < 2:
                continue

            # Use latest email's metadata as template
            sorted_emails = sorted(thread_emails, key=lambda e: e.get('metadata', {}).get('date', ''))
            template_meta = sorted_emails[-1].get('metadata', {})

            chunk = generate_thread_summary_chunk(conv_id, thread_emails, template_meta)
            if not chunk:
                continue

            # Skip if already exists
            try:
                existing = self._collection.get(ids=[chunk.chunk_id])
                if existing['ids']:
                    continue
            except Exception:
                pass

            ids.append(chunk.chunk_id)
            documents.append(chunk.text)
            metadatas.append(chunk.metadata)
            generated += 1

            # Batch insert every 100
            if len(ids) >= 100:
                embeddings = self._embed(documents)
                self._collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
                ids, documents, metadatas = [], [], []

        # Final batch
        if ids:
            embeddings = self._embed(documents)
            self._collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

        logger.info(f"Generated {generated} thread summary chunks from {len(threads)} threads")
        return generated
        
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
        """Get open email threads using the deterministic state engine.

        Delegates to ThreadStateEngine which uses known user email addresses
        to accurately classify thread status.
        """
        from state_engine import get_state_engine
        engine = get_state_engine()
        states = engine.get_all_thread_states(self)

        # Combine needs_action + awaiting_response + stale into a flat list
        open_items = []
        open_items.extend(states.get('needs_action', []))
        open_items.extend(states.get('awaiting_response', []))
        open_items.extend(states.get('stale', []))

        # Sort by date descending
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
        
    def get_analytics(self) -> Dict[str, Any]:
        """Get detailed email analytics for charts."""
        total = self._collection.count()
        if total == 0:
            return {
                'volume_by_date': {},
                'top_senders': [],
                'top_recipients': [],
                'hourly_distribution': {},
                'folder_distribution': {},
            }

        results = self._collection.get(
            include=["metadatas"],
            limit=min(total, 5000)
        )

        volume_by_date = {}
        sender_counts = {}
        recipient_counts = {}
        hourly = {str(h): 0 for h in range(24)}
        folder_dist = {}

        for meta in results['metadatas']:
            date_str = meta.get('date', '')
            direction = meta.get('direction', 'received')
            sender = meta.get('sender_name') or meta.get('sender', 'Unknown')
            recipients = meta.get('recipients', '')
            folder = meta.get('folder', 'Inbox')

            # Volume by date
            if date_str:
                try:
                    day = date_str[:10]  # YYYY-MM-DD
                    if day not in volume_by_date:
                        volume_by_date[day] = {'sent': 0, 'received': 0}
                    if direction == 'sent':
                        volume_by_date[day]['sent'] += 1
                    else:
                        volume_by_date[day]['received'] += 1

                    # Hourly distribution
                    if len(date_str) >= 13:
                        hour = date_str[11:13]
                        if hour.isdigit():
                            hourly[str(int(hour))] = hourly.get(str(int(hour)), 0) + 1
                except (ValueError, IndexError):
                    pass

            # Top senders (only received emails)
            if direction == 'received' and sender:
                sender_counts[sender] = sender_counts.get(sender, 0) + 1

            # Top recipients (only sent emails)
            if direction == 'sent' and recipients:
                for r in recipients.split(','):
                    r = r.strip()
                    if r:
                        recipient_counts[r] = recipient_counts.get(r, 0) + 1

            # Folder distribution
            if folder:
                folder_dist[folder] = folder_dist.get(folder, 0) + 1

        # Sort and limit
        top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        top_recipients = sorted(recipient_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            'volume_by_date': dict(sorted(volume_by_date.items())),
            'top_senders': [{'name': s[0], 'count': s[1]} for s in top_senders],
            'top_recipients': [{'name': r[0], 'count': r[1]} for r in top_recipients],
            'hourly_distribution': hourly,
            'folder_distribution': folder_dist,
        }

    def update_email_metadata(self, email_unique_id: str, metadata_updates: dict) -> int:
        """Update metadata on existing chunks for an email without re-embedding.

        Finds all chunks matching the email's unique_id prefix and updates
        their metadata fields.

        Returns count of chunks updated.
        """
        # Chunk IDs follow the pattern: {unique_id}_fresh, {unique_id}_quoted, etc.
        # Find all chunks for this email
        suffixes = ['_fresh', '_quoted', '_f0', '_f1', '_f2', '_q0', '_q1']
        chunk_ids = [f"{email_unique_id}{s}" for s in suffixes]
        # Also check attachment chunks (_att0, _att1, etc.)
        for i in range(10):
            chunk_ids.append(f"{email_unique_id}_att{i}")

        updated = 0
        try:
            existing = self._collection.get(ids=chunk_ids, include=["metadatas"])
            for i, chunk_id in enumerate(existing['ids']):
                meta = existing['metadatas'][i].copy()
                meta.update(metadata_updates)
                self._collection.update(
                    ids=[chunk_id],
                    metadatas=[meta],
                )
                updated += 1
        except Exception as e:
            logger.debug(f"Error updating metadata for {email_unique_id}: {e}")

        return updated

    def cleanup_old_emails(self, retention_days: int) -> int:
        """Delete documents with date metadata older than retention window.

        Returns count of deleted documents.
        """
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()

        total = self._collection.count()
        if total == 0:
            return 0

        # Query in batches to find old documents
        deleted = 0
        batch_size = 1000
        offset = 0

        while offset < total:
            results = self._collection.get(
                include=["metadatas"],
                limit=batch_size,
                offset=offset,
            )
            if not results['ids']:
                break

            old_ids = []
            for i, meta in enumerate(results['metadatas']):
                date_str = meta.get('date', '')
                if date_str and date_str < cutoff:
                    old_ids.append(results['ids'][i])

            if old_ids:
                # Delete in sub-batches (ChromaDB limit)
                for j in range(0, len(old_ids), 500):
                    batch_ids = old_ids[j:j + 500]
                    self._collection.delete(ids=batch_ids)
                    deleted += len(batch_ids)
                # Don't advance offset since we deleted items
                total = self._collection.count()
            else:
                offset += batch_size

        if deleted:
            logger.info(f"Retention cleanup: deleted {deleted} old chunks (>{retention_days} days)")

        return deleted

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
