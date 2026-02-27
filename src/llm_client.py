"""
LLM client - Ollama only. Direct HTTP, no wrappers.
"""

import logging
import json
from typing import List, Dict, Any, Optional, Generator
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)


class OllamaClient:
    """Direct Ollama API client."""
    
    SYSTEM_PROMPT = """You are an email assistant that ONLY analyzes emails shown to you.

CRITICAL RULES:
1. ONLY reference information that appears in the EMAIL CONTENT provided below
2. If no relevant emails are found, say "I don't see any emails matching that in your inbox"
3. NEVER make up email content, senders, subjects, or dates
4. Be specific - quote actual subjects and senders from the provided emails
5. If asked about emails you don't have, say so clearly

Your job is to:
- Identify action items and TO-DOs in the provided emails
- Note which emails need responses
- Highlight deadlines mentioned in the emails
- Summarize what the provided emails contain

CONVERSATION THREADS:
- Emails may be grouped into conversation threads showing the full back-and-forth
- Each thread has a STATUS: NEEDS YOUR ACTION, AWAITING RESPONSE, or COMPLETED
- "NEEDS YOUR ACTION" = the last message was received and you haven't replied
- "AWAITING RESPONSE" = you sent the last message and are waiting for their reply
- When asked about unanswered emails, use the thread status to determine this accurately
- When asked about a topic (e.g. "Vodafone"), find ALL threads and standalone emails mentioning it and summarize the full conversation history

Current date: {current_time}
"""

    def __init__(self,
                 base_url: str = "http://localhost:11434",
                 model: str = "llama3.1:8b",
                 temperature: float = 0.3):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature

    def set_temperature(self, temperature: float):
        """Update temperature at runtime."""
        self.temperature = max(0.0, min(1.0, temperature))
        logger.info(f"Temperature updated to {self.temperature}")

    def set_model(self, model: str):
        """Update model at runtime."""
        self.model = model
        logger.info(f"Model updated to {self.model}")

    def _format_single_email(self, email: Dict[str, Any], index: int) -> str:
        """Format a single email for LLM context."""
        meta = email.get('metadata', {})
        doc = email.get('document', '')

        if not doc or len(doc.strip()) < 10:
            logger.warning(f"Email {index} has empty or very short document: '{doc[:100]}'")

        sender = meta.get('sender_name') or meta.get('sender', 'Unknown')
        subject = meta.get('subject', 'No Subject')
        date = meta.get('date', 'Unknown date')
        direction = 'SENT BY ME' if meta.get('direction') == 'sent' else 'RECEIVED'
        is_replied = 'Yes' if meta.get('is_replied') else 'NO - NOT REPLIED'
        is_read = 'Read' if meta.get('is_read') else 'UNREAD'
        content = doc.strip() if doc else f"[Content not available. Subject: {subject}]"

        return f"""Direction: {direction}
From: {sender}
Subject: {subject}
Date: {date}
Status: {is_read} | Replied: {is_replied}

CONTENT:
{content[:2500]}
{"[...truncated...]" if len(content) > 2500 else ""}"""

    def _format_email_context(self, emails: List[Dict[str, Any]]) -> str:
        if not emails:
            return "No relevant emails found in the database."

        # Group emails by conversation_id for thread display
        threads = {}
        standalone = []
        for email in emails:
            conv_id = email.get('metadata', {}).get('conversation_id', '')
            if conv_id:
                threads.setdefault(conv_id, []).append(email)
            else:
                standalone.append(email)

        parts = []
        idx = 1

        # Render threaded conversations
        for conv_id, thread_emails in threads.items():
            # Sort thread by date
            thread_emails.sort(key=lambda e: e.get('metadata', {}).get('date', ''))

            # Determine thread status
            latest = thread_emails[-1]
            latest_meta = latest.get('metadata', {})
            last_dir = latest_meta.get('direction', 'received')
            last_replied = latest_meta.get('is_replied', False)

            if last_dir == 'sent' and not last_replied:
                thread_status = 'AWAITING RESPONSE (you sent last, no reply yet)'
            elif last_dir == 'received' and not last_replied:
                thread_status = 'NEEDS YOUR ACTION (received, not replied)'
            else:
                thread_status = 'COMPLETED'

            subject = latest_meta.get('subject', 'No Subject')
            parts.append(f"""
################################################################################
CONVERSATION THREAD: {subject}
Thread Status: {thread_status} | Messages: {len(thread_emails)}
################################################################################""")

            for email in thread_emails:
                parts.append(f"""
--- Message {idx} in thread ---
{self._format_single_email(email, idx)}""")
                idx += 1

        # Render standalone emails
        for email in standalone:
            parts.append(f"""
================================================================================
EMAIL #{idx} (standalone)
================================================================================
{self._format_single_email(email, idx)}""")
            idx += 1

        result = "\n".join(parts)
        logger.info(f"Formatted context: {idx - 1} emails ({len(threads)} threads + {len(standalone)} standalone), {len(result)} total chars")
        return result
        
    def chat(self,
             user_message: str,
             email_context: Optional[List[Dict[str, Any]]] = None) -> str:
        """Send message, get response."""
        
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        
        if email_context:
            context_str = self._format_email_context(email_context)
            logger.info(f"Sending {len(email_context)} emails to LLM ({len(context_str)} chars)")
            
            # Debug: print first 500 chars of context
            logger.debug(f"Context preview:\n{context_str[:500]}")
            
            prompt = f"""I am providing you with {len(email_context)} emails from the user's inbox below.
ONLY use information from these emails to answer the question. Do NOT make up any information.

===== START OF EMAILS FROM INBOX =====
{context_str}
===== END OF EMAILS FROM INBOX =====

USER'S QUESTION: {user_message}

INSTRUCTIONS:
- Answer based ONLY on the emails shown above
- Reference specific senders and subjects from the emails
- If no emails are relevant, say "I don't see any emails about that in the results"
- Be specific and quote from the actual email content when possible"""
        else:
            logger.warning("No email context provided to LLM")
            prompt = f"""No emails were found in the database for this query.

User asked: {user_message}

Please let the user know that no relevant emails were found and suggest they may need to ingest emails first."""
        
        # Combine system + user into single prompt for /api/generate
        full_prompt = f"{system}\n\n{prompt}"
        
        logger.debug(f"Prompt length: {len(full_prompt)} chars")
        
        # Use /api/generate endpoint (more compatible across Ollama versions)
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": self.temperature}
            },
            timeout=120.0
        )
        response.raise_for_status()
        return response.json()["response"]
    
    def chat_stream(self,
                    user_message: str,
                    email_context: Optional[List[Dict[str, Any]]] = None) -> Generator[str, None, None]:
        """Stream response chunks."""
        
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        
        if email_context:
            context_str = self._format_email_context(email_context)
            logger.info(f"Streaming with {len(email_context)} emails ({len(context_str)} chars)")
            prompt = f"""I am providing you with {len(email_context)} emails from the user's inbox below.
ONLY use information from these emails to answer the question. Do NOT make up any information.

===== START OF EMAILS FROM INBOX =====
{context_str}
===== END OF EMAILS FROM INBOX =====

USER'S QUESTION: {user_message}

INSTRUCTIONS:
- Answer based ONLY on the emails shown above
- Reference specific senders and subjects from the emails
- If no emails are relevant, say "I don't see any emails about that in the results"
- Be specific and quote from the actual email content when possible"""
        else:
            logger.warning("No email context for streaming")
            prompt = user_message
        
        # Combine system + user into single prompt
        full_prompt = f"{system}\n\n{prompt}"
        
        # Use /api/generate endpoint for streaming
        with httpx.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": full_prompt,
                "stream": True,
                "options": {"temperature": self.temperature}
            },
            timeout=120.0
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if token := data.get("response"):
                        yield token
                        
    MEETING_PREP_PROMPT = """You are preparing a meeting brief. Based ONLY on the emails provided below, create a preparation summary.

Meeting: {subject}
Time: {start} - {end}
Attendees: {attendees}
Location: {location}

Provide the following sections:

1. **Background**: What is this about? Summarize the email history on this topic.
2. **Key Topics**: Main discussion points from recent emails.
3. **Open Items**: Unresolved questions or pending actions.
4. **Recent Decisions**: Any decisions made in recent emails or meeting summaries.
5. **Your Action Items**: Things you need to address or bring up in this meeting.
6. **Meeting Notes Reference**: Any meeting summaries or notes found in the emails (e.g., Zoom meeting summaries).

CRITICAL: ONLY use information from the provided emails. If no relevant emails exist for a section, say "No information found." Do NOT make anything up.

Current date: {current_time}
"""

    def generate_meeting_prep(self,
                              meeting: Dict[str, Any],
                              email_context: List[Dict[str, Any]]) -> str:
        """Generate a meeting preparation brief."""

        attendees = ', '.join(meeting.get('all_attendees', [])[:10])
        system = self.MEETING_PREP_PROMPT.format(
            subject=meeting.get('subject', 'Unknown'),
            start=meeting.get('start', ''),
            end=meeting.get('end', ''),
            attendees=attendees or 'Not specified',
            location=meeting.get('location', 'Not specified'),
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        if email_context:
            context_str = self._format_email_context(email_context)
            prompt = f"""Here are {len(email_context)} relevant emails and meeting summaries found for this meeting topic:

===== START OF RELEVANT EMAILS =====
{context_str}
===== END OF RELEVANT EMAILS =====

Please prepare the meeting brief based on these emails."""
        else:
            prompt = "No relevant emails were found for this meeting topic. Please indicate that no prior email context is available and suggest the attendee ask colleagues for background."

        full_prompt = f"{system}\n\n{prompt}"

        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": self.temperature}
            },
            timeout=120.0
        )
        response.raise_for_status()
        return response.json()["response"]

    def generate_meeting_prep_stream(self,
                                     meeting: Dict[str, Any],
                                     email_context: List[Dict[str, Any]]) -> Generator[str, None, None]:
        """Stream a meeting preparation brief."""

        attendees = ', '.join(meeting.get('all_attendees', [])[:10])
        system = self.MEETING_PREP_PROMPT.format(
            subject=meeting.get('subject', 'Unknown'),
            start=meeting.get('start', ''),
            end=meeting.get('end', ''),
            attendees=attendees or 'Not specified',
            location=meeting.get('location', 'Not specified'),
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        if email_context:
            context_str = self._format_email_context(email_context)
            prompt = f"""Here are {len(email_context)} relevant emails and meeting summaries found for this meeting topic:

===== START OF RELEVANT EMAILS =====
{context_str}
===== END OF RELEVANT EMAILS =====

Please prepare the meeting brief based on these emails."""
        else:
            prompt = "No relevant emails were found for this meeting topic. Please indicate that no prior email context is available."

        full_prompt = f"{system}\n\n{prompt}"

        with httpx.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": full_prompt,
                "stream": True,
                "options": {"temperature": self.temperature}
            },
            timeout=120.0
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if token := data.get("response"):
                        yield token

    def is_available(self) -> bool:
        """Check if Ollama is running."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            return r.status_code == 200
        except:
            return False
            
    def list_models(self) -> List[str]:
        """Get available models."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
        except:
            pass
        return []


_client: Optional[OllamaClient] = None

def get_ollama_client() -> OllamaClient:
    global _client
    if _client is None:
        import config
        _client = OllamaClient(
            base_url=config.OLLAMA_URL,
            model=config.OLLAMA_MODEL,
            temperature=config.LLM_TEMPERATURE
        )
    return _client
