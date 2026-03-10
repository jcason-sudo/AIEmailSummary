"""
RAG Engine - retrieval + Ollama generation.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from vector_store import get_vector_store
from hybrid_search import get_hybrid_search
from llm_client import get_ollama_client, get_llm_client
from fact_store import get_fact_store

logger = logging.getLogger(__name__)


class RAGEngine:
    """Retrieval-Augmented Generation for email queries."""

    def __init__(self):
        self.vector_store = get_vector_store()
        self.hybrid = get_hybrid_search()
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

    def _get_fact_context(self, query: str) -> str:
        """Get relevant fact card data to augment query context."""
        try:
            fact_store = get_fact_store()
            if fact_store.get_extracted_count() == 0:
                return ""

            q = query.lower()
            parts = []

            # Check for commitment/promise queries
            if any(w in q for w in ['commit', 'promise', 'agreed', 'will do', 'pledged']):
                commitments = fact_store.get_commitments()
                if commitments:
                    parts.append("EXTRACTED COMMITMENTS:")
                    for c in commitments[:15]:
                        parts.append(f"  - {c['who']}: {c['what']}" + (f" (by {c['by_when']})" if c['by_when'] else ""))

            # Check for action item queries
            if any(w in q for w in ['action', 'todo', 'task', 'need to', 'should do']):
                actions = fact_store.get_action_items()
                if actions:
                    parts.append("EXTRACTED ACTION ITEMS:")
                    for a in actions[:15]:
                        parts.append(f"  - {a['description']}" + (f" (assigned: {a['assignee']})" if a['assignee'] else ""))

            if parts:
                return "\n\nSTRUCTURED DATA FROM EMAIL ANALYSIS:\n" + "\n".join(parts) + "\n"
        except Exception as e:
            logger.debug(f"Fact context lookup failed: {e}")

        return ""

    # Backend-specific context limits
    CONTEXT_LIMITS = {
        'claude': {'n_results': 30, 'max_content_chars': 2500},
        'local':  {'n_results': 15, 'max_content_chars': 1000},
    }

    def _get_llm(self, backend: Optional[str] = None):
        """Get LLM client — uses specified backend or default local client."""
        if backend:
            return get_llm_client(backend)
        return self.llm

    def _get_limits(self, backend: Optional[str] = None) -> dict:
        """Get context limits for the given backend."""
        return self.CONTEXT_LIMITS.get(backend or 'claude', self.CONTEXT_LIMITS['claude'])

    def query(self, user_query: str, n_results: int = None, backend: Optional[str] = None) -> Dict[str, Any]:
        """Process query and return answer with sources."""

        limits = self._get_limits(backend)
        if n_results is None:
            n_results = limits['n_results']

        query_info = self._detect_query_type(user_query)
        time_range = self._parse_time_reference(user_query)
        where = self._build_where_filter(query_info, time_range)

        # Retrieve relevant emails via hybrid search (BM25 + semantic + reranking)
        emails = self.hybrid.search(
            query=user_query,
            n_results=n_results,
            where=where
        )

        logger.info(f"Retrieved {len(emails)} emails for query: {user_query[:50]}...")

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)
        logger.info(f"Expanded to {len(emails_with_threads)} emails with thread context")

        # Generate response with LLM
        llm = self._get_llm(backend)
        ref_map = {}
        try:
            answer, ref_map = llm.chat(user_query, email_context=emails_with_threads,
                                       max_content_chars=limits['max_content_chars'])
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
                'message_id': meta.get('message_id', ''),
            })

        return {
            'answer': answer,
            'sources': sources,
            'ref_map': ref_map,
            'query_type': query_info.get('type', 'general'),
            'emails_found': len(emails),
            'threads_included': len(set(
                e.get('metadata', {}).get('conversation_id', '')
                for e in emails_with_threads
                if e.get('metadata', {}).get('conversation_id')
            ))
        }

    def query_stream(self, user_query: str, n_results: int = None, backend: Optional[str] = None):
        """Stream response chunks."""

        limits = self._get_limits(backend)
        if n_results is None:
            n_results = limits['n_results']

        query_info = self._detect_query_type(user_query)
        time_range = self._parse_time_reference(user_query)
        where = self._build_where_filter(query_info, time_range)

        emails = self.hybrid.search(query=user_query, n_results=n_results, where=where)

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)
        logger.info(f"Streaming: {len(emails)} search results expanded to {len(emails_with_threads)} with threads")

        # Stream LLM response with thread-expanded context
        llm = self._get_llm(backend)
        ref_map = {}
        for chunk in llm.chat_stream(user_query, email_context=emails_with_threads,
                                     max_content_chars=limits['max_content_chars']):
            # The LLM client yields a dict with __ref_map__ as the final item
            if isinstance(chunk, dict) and '__ref_map__' in chunk:
                ref_map = chunk['__ref_map__']
            else:
                yield {'type': 'chunk', 'content': chunk}

        # Send ref_map so frontend can resolve [SRC-N] inline citations
        if ref_map:
            yield {'type': 'ref_map', 'content': ref_map}

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
        """Get categorized open action items using deterministic state engine."""
        from state_engine import get_state_engine

        engine = get_state_engine()
        states = engine.get_all_thread_states(self.vector_store)

        needs_action = states.get('needs_action', [])
        awaiting_response = states.get('awaiting_response', [])
        stale = states.get('stale', [])

        # Use semantic search to tag deadline/question emails
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

        deadline_subjects = set(
            e.get('metadata', {}).get('subject', '') for e in deadline_emails
            if e.get('metadata', {}).get('subject')
        )
        question_subjects = set(
            e.get('metadata', {}).get('subject', '') for e in question_emails
            if e.get('metadata', {}).get('subject')
        )

        for item in needs_action:
            item['tags'] = []
            if item['subject'] in deadline_subjects:
                item['tags'].append('deadline')
            if item['subject'] in question_subjects:
                item['tags'].append('question')

        return {
            'needs_action': needs_action,
            'awaiting_response': awaiting_response,
            'stale': stale,
            'total_open': len(needs_action) + len(awaiting_response) + len(stale),
            'summary': {
                'needs_action_count': len(needs_action),
                'awaiting_response_count': len(awaiting_response),
                'stale_count': len(stale),
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
            results = self.hybrid.search(query=subject, n_results=10)
            add_results(results)

        # Search 2: by attendee names (top 3 to avoid too many queries)
        for attendee in attendees[:3]:
            if attendee:
                results = self.hybrid.search(query=attendee, n_results=5, rerank=False)
                add_results(results)

        # Search 3: meeting summary + subject (catches Zoom AI summaries via email)
        if subject:
            results = self.hybrid.search(
                query=f"meeting summary notes {subject}",
                n_results=5, rerank=False
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
            llm = self._get_llm('claude')
            brief = llm.generate_meeting_prep(meeting, emails_with_threads)
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
            add_results(self.hybrid.search(query=subject, n_results=10))
        for attendee in attendees[:3]:
            if attendee:
                add_results(self.hybrid.search(query=attendee, n_results=5, rerank=False))
        if subject:
            add_results(self.hybrid.search(query=f"meeting summary notes {subject}", n_results=5, rerank=False))

        emails_with_threads = self._expand_with_threads(all_emails)

        # Stream the brief via Claude
        llm = self._get_llm('claude')
        for token in llm.generate_meeting_prep_stream(meeting, emails_with_threads):
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

    def deep_research(self, topic: str, backend: Optional[str] = None) -> Dict[str, Any]:
        """Perform deep research on a topic across all related emails."""
        limits = self._get_limits(backend)

        # Broad hybrid search
        emails = self.hybrid.search(query=topic, n_results=50, rerank=False)

        # Filter to relevance > 0.3
        emails = [e for e in emails if e.get('relevance', 0) > 0.3]
        logger.info(f"Deep research '{topic}': {len(emails)} emails above relevance threshold")

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)

        # Group by conversation_id for timeline
        threads = {}
        standalone = []
        for e in emails_with_threads:
            conv_id = e.get('metadata', {}).get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(e)
            else:
                standalone.append(e)

        # Build timeline data
        timeline = []
        for conv_id, thread_emails in threads.items():
            thread_emails.sort(key=lambda x: x.get('metadata', {}).get('date', ''))
            first = thread_emails[0].get('metadata', {})
            last = thread_emails[-1].get('metadata', {})
            participants = list(set(
                e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', '')
                for e in thread_emails
                if e.get('metadata', {}).get('sender')
            ))

            last_dir = last.get('direction', 'received')
            last_replied = last.get('is_replied', False)
            if last_dir == 'received' and not last_replied:
                status = 'needs_action'
            elif last_dir == 'sent':
                status = 'awaiting_response'
            else:
                status = 'completed'

            timeline.append({
                'conversation_id': conv_id,
                'subject': last.get('subject', 'No Subject'),
                'date_start': first.get('date', ''),
                'date_end': last.get('date', ''),
                'message_count': len(thread_emails),
                'participants': participants,
                'status': status,
                'type': 'thread',
            })

        for e in standalone:
            meta = e.get('metadata', {})
            timeline.append({
                'conversation_id': '',
                'subject': meta.get('subject', 'No Subject'),
                'date_start': meta.get('date', ''),
                'date_end': meta.get('date', ''),
                'message_count': 1,
                'participants': [meta.get('sender_name') or meta.get('sender', '')],
                'status': 'standalone',
                'type': 'email',
            })

        # Sort timeline chronologically
        timeline.sort(key=lambda x: x['date_start'])

        # Generate synthesis via LLM
        llm = self._get_llm(backend)
        try:
            synthesis, ref_map = llm.research_synthesis(
                topic, emails_with_threads, max_content_chars=limits['max_content_chars']
            )
        except Exception as e:
            logger.error(f"Research synthesis error: {e}")
            synthesis = f"Error generating research synthesis: {e}"
            ref_map = {}

        return {
            'topic': topic,
            'total_emails': len(emails),
            'total_threads': len(threads),
            'timeline': timeline,
            'synthesis': synthesis,
            'ref_map': ref_map,
        }

    def deep_research_stream(self, topic: str, backend: Optional[str] = None):
        """Stream deep research synthesis, then send timeline metadata."""
        limits = self._get_limits(backend)

        # Broad hybrid search
        emails = self.hybrid.search(query=topic, n_results=50, rerank=False)
        emails = [e for e in emails if e.get('relevance', 0) > 0.3]

        # Expand with full thread context
        emails_with_threads = self._expand_with_threads(emails)

        # Group by conversation_id for timeline
        threads = {}
        standalone = []
        for e in emails_with_threads:
            conv_id = e.get('metadata', {}).get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(e)
            else:
                standalone.append(e)

        # Build timeline
        timeline = []
        for conv_id, thread_emails in threads.items():
            thread_emails.sort(key=lambda x: x.get('metadata', {}).get('date', ''))
            first = thread_emails[0].get('metadata', {})
            last = thread_emails[-1].get('metadata', {})
            participants = list(set(
                e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', '')
                for e in thread_emails
                if e.get('metadata', {}).get('sender')
            ))
            last_dir = last.get('direction', 'received')
            last_replied = last.get('is_replied', False)
            if last_dir == 'received' and not last_replied:
                status = 'needs_action'
            elif last_dir == 'sent':
                status = 'awaiting_response'
            else:
                status = 'completed'
            timeline.append({
                'conversation_id': conv_id,
                'subject': last.get('subject', 'No Subject'),
                'date_start': first.get('date', ''),
                'date_end': last.get('date', ''),
                'message_count': len(thread_emails),
                'participants': participants,
                'status': status,
                'type': 'thread',
            })
        for e in standalone:
            meta = e.get('metadata', {})
            timeline.append({
                'conversation_id': '',
                'subject': meta.get('subject', 'No Subject'),
                'date_start': meta.get('date', ''),
                'date_end': meta.get('date', ''),
                'message_count': 1,
                'participants': [meta.get('sender_name') or meta.get('sender', '')],
                'status': 'standalone',
                'type': 'email',
            })
        timeline.sort(key=lambda x: x['date_start'])

        # Stream LLM synthesis
        llm = self._get_llm(backend)
        ref_map = {}
        for chunk in llm.research_synthesis_stream(
            topic, emails_with_threads, max_content_chars=limits['max_content_chars']
        ):
            if isinstance(chunk, dict) and '__ref_map__' in chunk:
                ref_map = chunk['__ref_map__']
            else:
                yield {'type': 'chunk', 'content': chunk}

        if ref_map:
            yield {'type': 'ref_map', 'content': ref_map}

        # Send timeline and metadata at end
        yield {
            'type': 'metadata',
            'content': {
                'topic': topic,
                'total_emails': len(emails),
                'total_threads': len(threads),
                'timeline': timeline,
            }
        }

    def build_topic_map(self, topic: str) -> Dict[str, Any]:
        """Build an interactive topic map showing connections between emails and people."""
        emails = self.hybrid.search(query=topic, n_results=50, rerank=False)
        emails = [e for e in emails if e.get('relevance', 0) > 0.25]

        # Expand with threads
        emails_with_threads = self._expand_with_threads(emails)

        nodes = []
        edges = []
        node_ids = set()
        person_ids = {}  # email_address -> node_id

        # Group by conversation_id
        threads = {}
        standalone = []
        for e in emails_with_threads:
            conv_id = e.get('metadata', {}).get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(e)
            else:
                standalone.append(e)

        # Thread nodes
        for conv_id, thread_emails in threads.items():
            thread_emails.sort(key=lambda x: x.get('metadata', {}).get('date', ''))
            last_meta = thread_emails[-1].get('metadata', {})
            subject = last_meta.get('subject', 'No Subject')
            node_id = f"thread_{conv_id}"
            if node_id not in node_ids:
                node_ids.add(node_id)
                nodes.append({
                    'id': node_id,
                    'label': subject[:40],
                    'type': 'thread',
                    'subject': subject,
                    'message_count': len(thread_emails),
                    'date': last_meta.get('date', ''),
                })

            # Person nodes + edges
            for e in thread_emails:
                meta = e.get('metadata', {})
                sender = meta.get('sender', '')
                sender_name = meta.get('sender_name') or sender
                if sender:
                    pid = f"person_{sender}"
                    if pid not in node_ids:
                        node_ids.add(pid)
                        nodes.append({
                            'id': pid,
                            'label': sender_name,
                            'type': 'person',
                            'email': sender,
                        })
                        person_ids[sender] = pid
                    edge_id = f"{pid}->{node_id}"
                    if edge_id not in node_ids:
                        node_ids.add(edge_id)
                        edges.append({
                            'from': pid,
                            'to': node_id,
                            'label': 'participated',
                        })

        # Standalone email nodes
        for e in standalone:
            meta = e.get('metadata', {})
            email_id = e.get('id', '')
            node_id = f"email_{email_id}"
            if node_id not in node_ids:
                node_ids.add(node_id)
                nodes.append({
                    'id': node_id,
                    'label': (meta.get('subject', 'No Subject'))[:40],
                    'type': 'email',
                    'subject': meta.get('subject', ''),
                    'date': meta.get('date', ''),
                })

            sender = meta.get('sender', '')
            sender_name = meta.get('sender_name') or sender
            if sender:
                pid = f"person_{sender}"
                if pid not in node_ids:
                    node_ids.add(pid)
                    nodes.append({
                        'id': pid,
                        'label': sender_name,
                        'type': 'person',
                        'email': sender,
                    })
                edge_id = f"{pid}->{node_id}"
                if edge_id not in node_ids:
                    node_ids.add(edge_id)
                    edges.append({
                        'from': pid,
                        'to': node_id,
                        'label': 'sent',
                    })

        return {
            'nodes': nodes,
            'edges': edges,
            'stats': {
                'total_nodes': len(nodes),
                'total_edges': len(edges),
                'people': len([n for n in nodes if n['type'] == 'person']),
                'threads': len([n for n in nodes if n['type'] == 'thread']),
                'standalone': len([n for n in nodes if n['type'] == 'email']),
            }
        }

    def build_entity_map(self, subject: str) -> Dict[str, Any]:
        """Build an entity relationship map with tiered nodes, org detection, action items, and sentiment."""
        import itertools
        from collections import defaultdict

        # Use reranking for precision — only keep genuinely relevant emails
        emails = self.hybrid.search(query=subject, n_results=30, rerank=True)
        emails = [e for e in emails if e.get('relevance', 0) > 0.45]

        if not emails:
            return {'nodes': [], 'edges': [], 'stats': {
                'people': 0, 'topics': 0, 'organizations': 0,
                'connections': 0, 'total_emails': 0,
            }}

        # Only expand threads for high-relevance results (top half)
        top_emails = emails[:len(emails) // 2 + 1]
        emails_with_threads = self._expand_with_threads(top_emails)
        # Add remaining emails without thread expansion
        seen = {e.get('id', '') for e in emails_with_threads}
        for e in emails:
            if e.get('id', '') not in seen:
                emails_with_threads.append(e)
                seen.add(e.get('id', ''))

        def normalize_subject(s):
            return re.sub(r'^(RE:|Re:|FW:|Fwd:|re:|fw:)\s*', '', s).strip()

        # Check if a topic subject is related to the search query
        query_terms = set(subject.lower().split())

        def is_relevant_topic(subj):
            """Only include topics whose subject mentions the search term."""
            subj_lower = subj.lower()
            # Check if any query term appears in the subject
            for term in query_terms:
                if len(term) >= 2 and term in subj_lower:
                    return True
            return False

        # Group by conversation_id
        threads = {}
        standalone = []
        for e in emails_with_threads:
            conv_id = e.get('metadata', {}).get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(e)
            else:
                standalone.append(e)

        person_email_counts = {}
        person_names = {}
        topic_data = {}
        thread_participants = {}

        def norm_email(addr):
            """Normalize email/Exchange DN to lowercase for dedup."""
            return addr.strip().lower()

        for conv_id, thread_emails in threads.items():
            participants = set()
            for e in thread_emails:
                meta = e.get('metadata', {})
                sender = norm_email(meta.get('sender', ''))
                sender_name = meta.get('sender_name') or meta.get('sender', '')
                subj = normalize_subject(meta.get('subject', ''))
                is_meeting = meta.get('email_type') == 'meeting_note'
                # Don't treat bot senders as people, but keep topic tracking
                if sender and not is_meeting:
                    person_email_counts[sender] = person_email_counts.get(sender, 0) + 1
                    # Keep the best display name (prefer non-empty, non-email)
                    if sender_name and (sender not in person_names or person_names[sender] == sender):
                        person_names[sender] = sender_name
                    participants.add(sender)
                if subj and is_relevant_topic(subj):
                    if subj not in topic_data:
                        topic_data[subj] = {'conv_ids': set(), 'message_count': 0}
                    topic_data[subj]['conv_ids'].add(conv_id)
                    topic_data[subj]['message_count'] += 1
            thread_participants[conv_id] = participants

        for e in standalone:
            meta = e.get('metadata', {})
            sender = norm_email(meta.get('sender', ''))
            sender_name = meta.get('sender_name') or meta.get('sender', '')
            subj = normalize_subject(meta.get('subject', ''))
            is_meeting = meta.get('email_type') == 'meeting_note'
            if sender and not is_meeting:
                person_email_counts[sender] = person_email_counts.get(sender, 0) + 1
                if sender_name and (sender not in person_names or person_names[sender] == sender):
                    person_names[sender] = sender_name
            if subj and is_relevant_topic(subj):
                if subj not in topic_data:
                    topic_data[subj] = {'conv_ids': set(), 'message_count': 0}
                topic_data[subj]['message_count'] += 1

        # --- Organization detection ---
        IGNORED_DOMAINS = {'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com',
                           'live.com', 'aol.com', 'icloud.com', 'me.com', 'msn.com',
                           'googlemail.com', 'protonmail.com'}
        domain_people = defaultdict(set)
        for email_addr in person_email_counts:
            parts = email_addr.split('@')
            if len(parts) == 2:
                domain = parts[1].lower()
                if domain not in IGNORED_DOMAINS:
                    domain_people[domain].add(email_addr)

        org_nodes = {}
        for domain, members in domain_people.items():
            if len(members) >= 2:
                org_email_count = sum(person_email_counts.get(m, 0) for m in members)
                org_nodes[domain] = {
                    'id': f"org_{domain}",
                    'label': domain.split('.')[0].title(),
                    'type': 'organization',
                    'domain': domain,
                    'member_count': len(members),
                    'email_count': org_email_count,
                    'members': list(members),
                }

        # --- Tier assignment ---
        all_counts = sorted(person_email_counts.values()) if person_email_counts else [1]
        p66 = all_counts[int(len(all_counts) * 0.66)] if len(all_counts) >= 3 else max(all_counts)
        p33 = all_counts[int(len(all_counts) * 0.33)] if len(all_counts) >= 3 else 1

        def assign_tier(count):
            if count >= p66:
                return 'A'
            elif count >= p33:
                return 'B'
            return 'C'

        # --- Action item extraction and sentiment flags ---
        ACTION_PATTERNS = [
            re.compile(r'(?:^|\n)\s*(?:action[:\s]|todo[:\s]|to.do[:\s])(.+)', re.IGNORECASE),
            re.compile(r'(?:please|pls|kindly)\s+(.{10,80}?)[\.\n]', re.IGNORECASE),
            re.compile(r'(?:will|going to|need to|have to|must|should)\s+(.{10,80}?)[\.\n]', re.IGNORECASE),
            re.compile(r'(?:deadline|due(?:\s+date)?)[:\s]+(.{5,60})', re.IGNORECASE),
        ]
        SENTIMENT_KEYWORDS = {
            'urgent': 'urgent', 'asap': 'urgent', 'critical': 'urgent',
            'risk': 'risk', 'blocked': 'blocked', 'blocker': 'blocked',
            'overdue': 'overdue', 'waiting on': 'waiting', 'waiting for': 'waiting',
            'no response': 'waiting',
        }

        person_action_items = defaultdict(list)
        person_sentiments = defaultdict(set)

        for e in emails_with_threads:
            meta = e.get('metadata', {})
            sender = norm_email(meta.get('sender', ''))
            body = e.get('document', '')
            if not sender or not body:
                continue
            for pattern in ACTION_PATTERNS:
                for match in pattern.finditer(body):
                    item = match.group(1).strip()[:120]
                    if item and len(person_action_items[sender]) < 5:
                        if item not in person_action_items[sender]:
                            person_action_items[sender].append(item)
            body_lower = body.lower()
            for keyword, tag in SENTIMENT_KEYWORDS.items():
                if keyword in body_lower:
                    person_sentiments[sender].add(tag)

        # --- Build nodes ---
        nodes = []
        node_ids = set()

        for email_addr, count in person_email_counts.items():
            nid = f"person_{email_addr}"
            node_ids.add(nid)
            person_org = None
            for domain, org_data in org_nodes.items():
                if email_addr in org_data['members']:
                    person_org = org_data['id']
                    break
            nodes.append({
                'id': nid,
                'label': person_names.get(email_addr, email_addr),
                'type': 'person',
                'email': email_addr,
                'email_count': count,
                'tier': assign_tier(count),
                'organization': person_org,
                'action_items': person_action_items.get(email_addr, []),
                'sentiments': list(person_sentiments.get(email_addr, [])),
            })

        topic_counts = sorted([t['message_count'] for t in topic_data.values()]) if topic_data else [1]
        tp66 = topic_counts[int(len(topic_counts) * 0.66)] if len(topic_counts) >= 3 else max(topic_counts)
        tp33 = topic_counts[int(len(topic_counts) * 0.33)] if len(topic_counts) >= 3 else 1

        for subj, tdata in topic_data.items():
            nid = f"topic_{subj}"
            node_ids.add(nid)
            mc = tdata['message_count']
            tier = 'A' if mc >= tp66 else ('B' if mc >= tp33 else 'C')
            nodes.append({
                'id': nid,
                'label': subj[:50],
                'type': 'topic',
                'subject': subj,
                'message_count': mc,
                'tier': tier,
            })

        for domain, org_data in org_nodes.items():
            org_data['tier'] = assign_tier(org_data['email_count'])
            nodes.append(org_data)
            node_ids.add(org_data['id'])

        # --- Build edges ---
        edges = []
        edge_set = set()

        # Reply/quote counts per thread
        thread_reply_counts = {}
        thread_quote_counts = {}
        for conv_id, thread_emails in threads.items():
            replies = sum(1 for e in thread_emails if e.get('metadata', {}).get('is_replied'))
            quotes = sum(1 for e in thread_emails if '>' in e.get('document', '')[:200])
            thread_reply_counts[conv_id] = replies
            thread_quote_counts[conv_id] = quotes

        # Person↔Person edges
        person_pair_threads = defaultdict(list)
        for conv_id, participants in thread_participants.items():
            for p1, p2 in itertools.combinations(sorted(participants), 2):
                person_pair_threads[(p1, p2)].append(conv_id)

        for (p1, p2), conv_ids in person_pair_threads.items():
            total_replies = sum(thread_reply_counts.get(c, 0) for c in conv_ids)
            total_quotes = sum(thread_quote_counts.get(c, 0) for c in conv_ids)
            eid = f"person_{p1}<->person_{p2}"
            if eid not in edge_set:
                edge_set.add(eid)
                edges.append({
                    'from': f"person_{p1}",
                    'to': f"person_{p2}",
                    'weight': len(conv_ids),
                    'type': 'person_person',
                    'label': str(len(conv_ids)),
                    'thread_ids': conv_ids,
                    'reply_count': total_replies,
                    'quote_count': total_quotes,
                })

        # Person→Topic edges
        person_topic_threads = defaultdict(lambda: {'count': 0, 'thread_ids': []})
        for e in emails_with_threads:
            meta = e.get('metadata', {})
            sender = norm_email(meta.get('sender', ''))
            subj = normalize_subject(meta.get('subject', ''))
            conv_id = meta.get('conversation_id', '')
            if sender and subj and f"topic_{subj}" in node_ids:
                key = (sender, subj)
                person_topic_threads[key]['count'] += 1
                if conv_id and conv_id not in person_topic_threads[key]['thread_ids']:
                    person_topic_threads[key]['thread_ids'].append(conv_id)

        for (email_addr, subj), data in person_topic_threads.items():
            eid = f"person_{email_addr}->topic_{subj}"
            if eid not in edge_set:
                edge_set.add(eid)
                edges.append({
                    'from': f"person_{email_addr}",
                    'to': f"topic_{subj}",
                    'weight': data['count'],
                    'type': 'person_topic',
                    'thread_ids': data['thread_ids'],
                })

        # Person→Organization membership edges
        for domain, org_data in org_nodes.items():
            for member_email in org_data['members']:
                person_nid = f"person_{member_email}"
                if person_nid in node_ids:
                    eid = f"{person_nid}->{org_data['id']}"
                    if eid not in edge_set:
                        edge_set.add(eid)
                        edges.append({
                            'from': person_nid,
                            'to': org_data['id'],
                            'weight': 1,
                            'type': 'person_org',
                            'thread_ids': [],
                        })

        return {
            'nodes': nodes,
            'edges': edges,
            'stats': {
                'people': len([n for n in nodes if n['type'] == 'person']),
                'topics': len([n for n in nodes if n['type'] == 'topic']),
                'organizations': len([n for n in nodes if n['type'] == 'organization']),
                'connections': len(edges),
                'total_emails': len(emails),
            }
        }

    def get_meetings(self, days: int = 7) -> Dict[str, Any]:
        """Get upcoming meetings from Outlook Calendar."""
        from calendar_connection import get_calendar_meetings
        return get_calendar_meetings(days=days)


_engine: Optional[RAGEngine] = None

def get_rag_engine() -> RAGEngine:
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine
