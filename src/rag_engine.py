"""
RAG Engine - retrieval + Ollama generation.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from vector_store import get_vector_store
from llm_client import get_ollama_client

logger = logging.getLogger(__name__)


class RAGEngine:
    """Retrieval-Augmented Generation for email queries."""

    def __init__(self):
        self.vector_store = get_vector_store()
        self.llm = get_ollama_client()

    def _parse_time_reference(self, query: str) -> Optional[Tuple[datetime, datetime]]:
        """Extract time range from query."""
        q = query.lower()
        now = datetime.now()

        if 'today' in q:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, now)
        if 'yesterday' in q:
            yesterday = now - timedelta(days=1)
            return (
                yesterday.replace(hour=0, minute=0, second=0, microsecond=0),
                yesterday.replace(hour=23, minute=59, second=59)
            )
        if 'this week' in q:
            start = now - timedelta(days=now.weekday())
            return (start.replace(hour=0, minute=0, second=0, microsecond=0), now)
        if 'last week' in q:
            start = now - timedelta(days=now.weekday() + 7)
            end = start + timedelta(days=6, hours=23, minutes=59)
            return (start.replace(hour=0, minute=0, second=0, microsecond=0), end)

        # "last N days"
        match = re.search(r'(?:past|last)\s+(\d+)\s+days?', q)
        if match:
            days = int(match.group(1))
            return (now - timedelta(days=days), now)

        return None

    def _detect_query_type(self, query: str) -> Dict[str, Any]:
        """Detect what kind of query this is."""
        q = query.lower()

        result = {"filters": {}}

        # Action items / TO-DOs
        if any(word in q for word in ['action', 'todo', 'to-do', 'need to', 'should', 'must', 'pending', 'waiting']):
            result["type"] = "action_needed"
            result["filters"]["direction"] = "received"
            result["filters"]["is_replied"] = False

        # Follow-ups on sent emails
        elif any(phrase in q for phrase in ['i sent', 'my sent', 'haven\'t heard', 'no response', 'waiting for reply', 'follow up']):
            result["type"] = "sent_followup"
            result["filters"]["direction"] = "sent"
            result["filters"]["is_replied"] = False

        # Unread
        elif 'unread' in q:
            result["type"] = "unread"
            result["filters"]["is_read"] = False
            result["filters"]["direction"] = "received"

        else:
            result["type"] = "general"

        return result

    def _build_where_filter(self, query_info: Dict[str, Any], time_range: Optional[Tuple[datetime, datetime]]) -> Optional[Dict]:
        """Build ChromaDB where filter from query analysis."""
        where = None
        if query_info["filters"]:
            conditions = [{k: v} for k, v in query_info["filters"].items()]
            if len(conditions) == 1:
                where = conditions[0]
            elif conditions:
                where = {"$and": conditions}

        if time_range:
            start, end = time_range
            time_conditions = [
                {"date": {"$gte": start.isoformat()}},
                {"date": {"$lte": end.isoformat()}}
            ]
            if where:
                if "$and" in where:
                    where["$and"].extend(time_conditions)
                else:
                    where = {"$and": [where] + time_conditions}
            else:
                where = {"$and": time_conditions}

        return where

    def _expand_with_threads(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Expand search results with full conversation threads.

        For each email that has a conversation_id, fetch all emails in that thread
        so the LLM has complete conversation context.
        """
        seen_ids = set()
        seen_conv_ids = set()
        expanded = []

        for email in emails:
            email_id = email.get('id', '')
            if email_id in seen_ids:
                continue
            seen_ids.add(email_id)

            conv_id = email.get('metadata', {}).get('conversation_id', '')
            if conv_id and conv_id not in seen_conv_ids:
                seen_conv_ids.add(conv_id)
                # Fetch the full thread
                thread_emails = self.vector_store.get_thread_emails(conv_id)
                for te in thread_emails:
                    te_id = te.get('id', '')
                    if te_id not in seen_ids:
                        seen_ids.add(te_id)
                        expanded.append(te)
                # Also include the original if not already added from thread
                if email_id not in {e.get('id', '') for e in expanded}:
                    expanded.append(email)
            else:
                expanded.append(email)

        return expanded

    def query(self, user_query: str, n_results: int = 5) -> Dict[str, Any]:
        """Process query and return answer with sources."""

        query_info = self._detect_query_type(user_query)
        time_range = self._parse_time_reference(user_query)
        where = self._build_where_filter(query_info, time_range)

        # Retrieve relevant emails
        emails = self.vector_store.search(
            query=user_query,
            n_results=n_results,
            where=where
        )

        logger.info(f"Retrieved {len(emails)} emails for query: {user_query[:50]}...")

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)
        logger.info(f"Expanded to {len(emails_with_threads)} emails with thread context")

        # Generate response with LLM
        try:
            answer = self.llm.chat(user_query, email_context=emails_with_threads)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            answer = f"Error generating response: {e}"

        # Format sources (from original search results, not expanded)
        sources = []
        for email in emails[:10]:
            meta = email.get('metadata', {})
            sources.append({
                'sender': meta.get('sender_name') or meta.get('sender', ''),
                'subject': meta.get('subject', ''),
                'date': meta.get('date', ''),
                'relevance': round(email.get('relevance', 0) * 100, 1),
                'conversation_id': meta.get('conversation_id', ''),
            })

        return {
            'answer': answer,
            'sources': sources,
            'query_type': query_info.get('type', 'general'),
            'emails_found': len(emails),
            'threads_included': len(set(
                e.get('metadata', {}).get('conversation_id', '')
                for e in emails_with_threads
                if e.get('metadata', {}).get('conversation_id')
            ))
        }

    def query_stream(self, user_query: str, n_results: int = 5):
        """Stream response chunks."""

        query_info = self._detect_query_type(user_query)
        time_range = self._parse_time_reference(user_query)
        where = self._build_where_filter(query_info, time_range)

        emails = self.vector_store.search(query=user_query, n_results=n_results, where=where)

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)
        logger.info(f"Streaming: {len(emails)} search results expanded to {len(emails_with_threads)} with threads")

        # Stream LLM response with thread-expanded context
        for chunk in self.llm.chat_stream(user_query, email_context=emails_with_threads):
            yield {'type': 'chunk', 'content': chunk}

        # Send sources at end
        sources = [
            {
                'sender': e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', ''),
                'subject': e.get('metadata', {}).get('subject', ''),
                'date': e.get('metadata', {}).get('date', ''),
                'conversation_id': e.get('metadata', {}).get('conversation_id', ''),
            }
            for e in emails[:10]
        ]
        yield {'type': 'sources', 'content': sources}

    def get_summary(self) -> Dict[str, Any]:
        """Get inbox summary."""
        stats = self.vector_store.get_stats()

        action_needed = self.vector_store.search(
            query="action required response needed deadline",
            n_results=5,
            where={"$and": [{"direction": "received"}, {"is_replied": False}]}
        )

        awaiting = self.vector_store.search(
            query="sent waiting for response",
            n_results=5,
            where={"$and": [{"direction": "sent"}, {"is_replied": False}]}
        )

        return {
            'stats': stats,
            'action_needed': [
                {
                    'sender': e['metadata'].get('sender', ''),
                    'subject': e['metadata'].get('subject', ''),
                    'date': e['metadata'].get('date', ''),
                }
                for e in action_needed
            ],
            'awaiting_response': [
                {
                    'recipient': e['metadata'].get('recipients', '').split(',')[0],
                    'subject': e['metadata'].get('subject', ''),
                    'date': e['metadata'].get('date', ''),
                }
                for e in awaiting
            ]
        }

    def get_tasks(self) -> Dict[str, Any]:
        """Get categorized open action items with thread context."""

        # Get all open items from vector store
        open_items = self.vector_store.get_open_items()

        # Separate by status
        needs_action = [item for item in open_items if item['status'] == 'needs_action']
        awaiting_response = [item for item in open_items if item['status'] == 'awaiting_response']

        # Use semantic search to find deadline/question emails
        deadline_emails = self.vector_store.search(
            query="deadline due date by end of urgent asap",
            n_results=10,
            where={"$and": [{"direction": "received"}, {"is_replied": False}]}
        )

        question_emails = self.vector_store.search(
            query="question can you could you please let me know",
            n_results=10,
            where={"$and": [{"direction": "received"}, {"is_replied": False}]}
        )

        # Build sets of subjects that match deadline/question patterns
        deadline_subjects = set()
        for e in deadline_emails:
            subj = e.get('metadata', {}).get('subject', '')
            if subj:
                deadline_subjects.add(subj)

        question_subjects = set()
        for e in question_emails:
            subj = e.get('metadata', {}).get('subject', '')
            if subj:
                question_subjects.add(subj)

        # Tag items
        for item in needs_action:
            item['tags'] = []
            if item['subject'] in deadline_subjects:
                item['tags'].append('deadline')
            if item['subject'] in question_subjects:
                item['tags'].append('question')

        return {
            'needs_action': needs_action,
            'awaiting_response': awaiting_response,
            'total_open': len(needs_action) + len(awaiting_response),
            'summary': {
                'needs_action_count': len(needs_action),
                'awaiting_response_count': len(awaiting_response),
                'with_deadlines': len([i for i in needs_action if 'deadline' in i.get('tags', [])]),
                'with_questions': len([i for i in needs_action if 'question' in i.get('tags', [])]),
            }
        }


    def prepare_for_meeting(self, meeting: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare a brief for a specific meeting by searching relevant emails.

        Searches by:
        1. Meeting subject (e.g., "Vodafone")
        2. Attendee names/emails
        3. "meeting summary" + subject (catches Zoom AI summaries)
        """
        subject = meeting.get('subject', '')
        attendees = meeting.get('all_attendees', [])

        all_emails = []
        seen_ids = set()

        def add_results(results):
            for r in results:
                rid = r.get('id', '')
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    all_emails.append(r)

        # Search 1: by meeting subject
        if subject:
            results = self.vector_store.search(query=subject, n_results=10)
            add_results(results)

        # Search 2: by attendee names (top 3 to avoid too many queries)
        for attendee in attendees[:3]:
            if attendee:
                results = self.vector_store.search(query=attendee, n_results=5)
                add_results(results)

        # Search 3: meeting summary + subject (catches Zoom AI summaries via email)
        if subject:
            results = self.vector_store.search(
                query=f"meeting summary notes {subject}",
                n_results=5
            )
            add_results(results)

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(all_emails)
        logger.info(
            f"Meeting prep for '{subject}': {len(all_emails)} unique emails, "
            f"{len(emails_with_threads)} with threads"
        )

        # Generate prep brief
        try:
            brief = self.llm.generate_meeting_prep(meeting, emails_with_threads)
        except Exception as e:
            logger.error(f"Meeting prep LLM error: {e}")
            brief = f"Error generating meeting prep: {e}"

        return {
            'meeting': {
                'subject': subject,
                'start': meeting.get('start', ''),
                'end': meeting.get('end', ''),
                'location': meeting.get('location', ''),
                'organizer': meeting.get('organizer', ''),
                'attendees': attendees,
                'is_all_day': meeting.get('is_all_day', False),
            },
            'brief': brief,
            'emails_found': len(all_emails),
            'threads_found': len(set(
                e.get('metadata', {}).get('conversation_id', '')
                for e in emails_with_threads
                if e.get('metadata', {}).get('conversation_id')
            )),
            'sources': [
                {
                    'sender': e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', ''),
                    'subject': e.get('metadata', {}).get('subject', ''),
                    'date': e.get('metadata', {}).get('date', ''),
                }
                for e in all_emails[:10]
            ]
        }

    def prepare_for_meeting_stream(self, meeting: Dict[str, Any]):
        """Stream a meeting prep brief."""
        subject = meeting.get('subject', '')
        attendees = meeting.get('all_attendees', [])

        all_emails = []
        seen_ids = set()

        def add_results(results):
            for r in results:
                rid = r.get('id', '')
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    all_emails.append(r)

        if subject:
            add_results(self.vector_store.search(query=subject, n_results=10))
        for attendee in attendees[:3]:
            if attendee:
                add_results(self.vector_store.search(query=attendee, n_results=5))
        if subject:
            add_results(self.vector_store.search(query=f"meeting summary notes {subject}", n_results=5))

        emails_with_threads = self._expand_with_threads(all_emails)

        # Stream the brief
        for token in self.llm.generate_meeting_prep_stream(meeting, emails_with_threads):
            yield {'type': 'chunk', 'content': token}

        # Send metadata at end
        yield {
            'type': 'metadata',
            'content': {
                'emails_found': len(all_emails),
                'sources': [
                    {
                        'sender': e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', ''),
                        'subject': e.get('metadata', {}).get('subject', ''),
                    }
                    for e in all_emails[:10]
                ]
            }
        }

    def get_meetings(self) -> Dict[str, Any]:
        """Get next business day meetings from Outlook Calendar."""
        from calendar_connection import get_calendar_meetings
        return get_calendar_meetings()


_engine: Optional[RAGEngine] = None

def get_rag_engine() -> RAGEngine:
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine
