"""
Deterministic thread state engine.

Classifies email threads using rules based on sender identity,
not unreliable is_replied flags.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

STALE_DAYS = 14


class ThreadStateEngine:
    """Rules-based thread status classification.

    Uses known user email addresses to determine who sent each message,
    then applies deterministic rules to classify thread status.
    """

    def __init__(self, my_addresses: List[str]):
        self.my_addresses = set(addr.lower() for addr in my_addresses)
        if not self.my_addresses:
            logger.warning("No user email addresses configured — state engine accuracy will be limited")

    def _is_me(self, sender: str) -> bool:
        """Check if a sender address belongs to the user."""
        if not sender:
            return False
        sender_lower = sender.lower().strip()
        # Direct match
        if sender_lower in self.my_addresses:
            return True
        # Check if any of my addresses appear in the sender string
        # (handles "James Cason <jcason@gmail.com>" format)
        for addr in self.my_addresses:
            if addr in sender_lower:
                return True
        return False

    def classify_thread(self, messages: List[dict]) -> str:
        """Classify a thread's status based on message sequence.

        Args:
            messages: List of metadata dicts, each with 'sender', 'date',
                      'direction', 'is_replied' keys.

        Returns:
            One of: 'needs_action', 'awaiting_response', 'completed', 'stale'
        """
        if not messages:
            return 'completed'

        # Sort by date
        sorted_msgs = sorted(messages, key=lambda m: m.get('date', ''))
        latest = sorted_msgs[-1]

        # Determine who sent the last message
        last_sender = latest.get('sender', '')
        last_is_me = self._is_me(last_sender)

        # If we don't have address info, fall back to direction field
        if not self.my_addresses:
            last_is_me = latest.get('direction', 'received') == 'sent'

        # Check staleness
        last_date_str = latest.get('date', '')
        if last_date_str:
            try:
                last_date = datetime.fromisoformat(last_date_str)
                if datetime.now() - last_date > timedelta(days=STALE_DAYS):
                    # Still classify the underlying state, but mark as stale
                    if not last_is_me:
                        return 'stale_needs_action'
                    return 'stale'
            except (ValueError, TypeError):
                pass

        # Rule 1: I sent the last message → awaiting response
        if last_is_me:
            return 'awaiting_response'

        # Rule 2: Someone else sent the last message
        # Check if I replied after receiving it
        last_replied = latest.get('is_replied', False)
        if last_replied:
            return 'completed'

        # I haven't replied → needs my action
        return 'needs_action'

    def classify_standalone(self, meta: dict) -> str:
        """Classify a standalone email (no conversation thread)."""
        sender = meta.get('sender', '')
        is_me = self._is_me(sender)

        if not self.my_addresses:
            is_me = meta.get('direction', 'received') == 'sent'

        if is_me:
            return 'awaiting_response'

        if meta.get('is_replied', False):
            return 'completed'

        # Check staleness
        date_str = meta.get('date', '')
        if date_str:
            try:
                msg_date = datetime.fromisoformat(date_str)
                if datetime.now() - msg_date > timedelta(days=STALE_DAYS):
                    return 'stale_needs_action'
            except (ValueError, TypeError):
                pass

        return 'needs_action'

    def get_all_thread_states(self, store) -> Dict[str, List[dict]]:
        """Classify all threads and standalone emails in the store.

        Returns dict with keys: needs_action, awaiting_response, stale, completed
        Each value is a list of item dicts.
        """
        try:
            total = store._collection.count()
            if total == 0:
                return {'needs_action': [], 'awaiting_response': [], 'stale': [], 'completed': []}
            results = store._collection.get(
                include=["metadatas"],
                limit=min(total, 5000)
            )
        except Exception as e:
            logger.warning(f"State engine query failed: {e}")
            return {'needs_action': [], 'awaiting_response': [], 'stale': [], 'completed': []}

        # Group by conversation_id
        threads = {}
        standalone = []
        for i in range(len(results['ids'])):
            meta = results['metadatas'][i] if results['metadatas'] else {}
            meta['_id'] = results['ids'][i]
            conv_id = meta.get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(meta)
            else:
                standalone.append(meta)

        categorized = {
            'needs_action': [],
            'awaiting_response': [],
            'stale': [],
            'completed': [],
        }

        # Classify threads
        for conv_id, messages in threads.items():
            # Skip threads that are purely meeting notes (Zoom, etc.)
            if all(m.get('email_type') == 'meeting_note' for m in messages):
                continue

            status = self.classify_thread(messages)

            # Skip completed
            if status == 'completed':
                continue

            messages.sort(key=lambda m: m.get('date', ''))
            latest = messages[-1]

            # Map stale variants
            if status.startswith('stale'):
                bucket = 'stale'
            else:
                bucket = status

            categorized[bucket].append({
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

        # Classify standalone emails
        for meta in standalone:
            if meta.get('email_type') == 'meeting_note':
                continue
            status = self.classify_standalone(meta)
            if status == 'completed':
                continue

            bucket = 'stale' if status.startswith('stale') else status

            categorized[bucket].append({
                'conversation_id': '',
                'subject': meta.get('subject', ''),
                'sender': meta.get('sender', ''),
                'sender_name': meta.get('sender_name', ''),
                'date': meta.get('date', ''),
                'message_count': 1,
                'status': status,
                'participants': [meta.get('sender', '')]
            })

        # Sort each bucket by date descending
        for bucket in categorized:
            categorized[bucket].sort(key=lambda x: x['date'], reverse=True)

        logger.info(
            f"State engine: {len(categorized['needs_action'])} needs_action, "
            f"{len(categorized['awaiting_response'])} awaiting, "
            f"{len(categorized['stale'])} stale"
        )
        return categorized


# Module-level singleton
_engine: Optional[ThreadStateEngine] = None


def get_state_engine() -> ThreadStateEngine:
    global _engine
    if _engine is None:
        import config
        _engine = ThreadStateEngine(config.MY_EMAIL_ADDRESSES)
    return _engine
