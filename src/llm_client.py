"""
LLM client - supports llama.cpp (local GPU) and Claude API (cloud).
"""

import logging
import json
from typing import List, Dict, Any, Optional, Generator
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)


class BaseLLMClient:
    """Shared logic for all LLM backends."""

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

    def __init__(self, temperature: float = 0.3):
        self.temperature = temperature

    def set_temperature(self, temperature: float):
        """Update temperature at runtime."""
        self.temperature = max(0.0, min(1.0, temperature))
        logger.info(f"Temperature updated to {self.temperature}")

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
{content[:1000]}
{"[...truncated...]" if len(content) > 1000 else ""}"""

    def _format_email_context(self, emails: List[Dict[str, Any]]) -> str:
        if not emails:
            return "No relevant emails found in the database."

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

        for conv_id, thread_emails in threads.items():
            thread_emails.sort(key=lambda e: e.get('metadata', {}).get('date', ''))

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

    def _build_prompt(self, user_message: str, email_context: Optional[List[Dict[str, Any]]] = None) -> str:
        """Build the user prompt from message and email context."""
        if email_context:
            context_str = self._format_email_context(email_context)
            logger.info(f"Sending {len(email_context)} emails to LLM ({len(context_str)} chars)")
            logger.debug(f"Context preview:\n{context_str[:500]}")

            return f"""I am providing you with {len(email_context)} emails from the user's inbox below.
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
            return f"""No emails were found in the database for this query.

User asked: {user_message}

Please let the user know that no relevant emails were found and suggest they may need to ingest emails first."""

    def _build_meeting_context(self, meeting: Dict[str, Any], email_context: List[Dict[str, Any]]):
        """Build system prompt and user prompt for meeting prep."""
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

        return system, prompt

    # Public interface - subclasses must implement these
    def chat(self, user_message: str, email_context: Optional[List[Dict[str, Any]]] = None) -> str:
        raise NotImplementedError

    def chat_stream(self, user_message: str, email_context: Optional[List[Dict[str, Any]]] = None) -> Generator[str, None, None]:
        raise NotImplementedError

    def generate_meeting_prep(self, meeting: Dict[str, Any], email_context: List[Dict[str, Any]]) -> str:
        raise NotImplementedError

    def generate_meeting_prep_stream(self, meeting: Dict[str, Any], email_context: List[Dict[str, Any]]) -> Generator[str, None, None]:
        raise NotImplementedError

    def is_available(self) -> bool:
        raise NotImplementedError

    def list_models(self) -> List[str]:
        raise NotImplementedError


class LlamaCppClient(BaseLLMClient):
    """llama.cpp server client using OpenAI-compatible API."""

    def __init__(self,
                 base_url: str = "http://localhost:8080",
                 model: str = "llama-3.2-3b-instruct",
                 temperature: float = 0.3):
        super().__init__(temperature)
        self.base_url = base_url.rstrip('/')
        self.model = model

    def set_model(self, model: str):
        """Update model at runtime."""
        self.model = model
        logger.info(f"Model updated to {self.model}")

    def _call_api(self, system: str, prompt: str, stream: bool = False):
        """Make a request to llama.cpp OpenAI-compatible API."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "temperature": self.temperature,
            "max_tokens": 1024,
            "stream": stream
        }
        if stream:
            return httpx.stream("POST", f"{self.base_url}/v1/chat/completions",
                                json=payload, timeout=300.0)
        else:
            response = httpx.post(f"{self.base_url}/v1/chat/completions",
                                  json=payload, timeout=300.0)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def _call_api_with_retry(self, system: str, prompt: str, email_context: Optional[List[Dict[str, Any]]] = None, stream: bool = False):
        """Call API with graceful context overflow retry.

        If the local LLM returns a 400 (context too long), retry with progressively
        fewer emails by removing the oldest ones first.
        """
        try:
            return self._call_api(system, prompt, stream=stream)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400 or email_context is None:
                raise

            # Context overflow — retry with fewer emails
            logger.warning(f"Context overflow (400) with {len(email_context)} emails, retrying with fewer")
            remaining = email_context.copy()

            for attempt in range(3):
                # Remove ~1/3 of remaining emails (oldest first by date)
                cut = max(1, len(remaining) // 3)
                remaining.sort(key=lambda e: e.get('metadata', {}).get('date', ''))
                remaining = remaining[cut:]
                logger.info(f"Retry {attempt + 1}: reduced to {len(remaining)} emails")

                new_prompt = self._build_prompt(
                    prompt.split("USER'S QUESTION: ")[-1].split("\n\nINSTRUCTIONS:")[0] if "USER'S QUESTION:" in prompt else prompt,
                    remaining
                )

                try:
                    return self._call_api(system, new_prompt, stream=stream)
                except httpx.HTTPStatusError as e2:
                    if e2.response.status_code != 400:
                        raise
                    continue

            raise RuntimeError("Context too long even after reducing to minimum emails")

    def _stream_tokens(self, system: str, prompt: str) -> Generator[str, None, None]:
        """Stream tokens from llama.cpp server."""
        with self._call_api(system, prompt, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    data = json.loads(line[6:])
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    if token := delta.get("content"):
                        yield token

    def _stream_tokens_with_retry(self, system: str, prompt: str, email_context: Optional[List[Dict[str, Any]]] = None) -> Generator[str, None, None]:
        """Stream tokens with graceful context overflow retry."""
        try:
            yield from self._stream_tokens(system, prompt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400 or email_context is None:
                raise

            logger.warning(f"Context overflow (400) during streaming with {len(email_context)} emails, retrying with fewer")
            remaining = email_context.copy()

            for attempt in range(3):
                cut = max(1, len(remaining) // 3)
                remaining.sort(key=lambda e: e.get('metadata', {}).get('date', ''))
                remaining = remaining[cut:]
                logger.info(f"Stream retry {attempt + 1}: reduced to {len(remaining)} emails")

                # Extract original user question from prompt
                if "USER'S QUESTION:" in prompt:
                    user_msg = prompt.split("USER'S QUESTION: ")[-1].split("\n\nINSTRUCTIONS:")[0]
                else:
                    user_msg = prompt
                new_prompt = self._build_prompt(user_msg, remaining)

                try:
                    yield from self._stream_tokens(system, new_prompt)
                    return
                except httpx.HTTPStatusError as e2:
                    if e2.response.status_code != 400:
                        raise
                    continue

            raise RuntimeError("Context too long even after reducing to minimum emails")

    def chat(self,
             user_message: str,
             email_context: Optional[List[Dict[str, Any]]] = None) -> str:
        """Send message, get response."""
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        prompt = self._build_prompt(user_message, email_context)
        logger.debug(f"Prompt length: {len(prompt)} chars")
        return self._call_api_with_retry(system, prompt, email_context)

    def chat_stream(self,
                    user_message: str,
                    email_context: Optional[List[Dict[str, Any]]] = None) -> Generator[str, None, None]:
        """Stream response chunks."""
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        prompt = self._build_prompt(user_message, email_context)
        yield from self._stream_tokens_with_retry(system, prompt, email_context)

    def generate_meeting_prep(self,
                              meeting: Dict[str, Any],
                              email_context: List[Dict[str, Any]]) -> str:
        """Generate a meeting preparation brief."""
        system, prompt = self._build_meeting_context(meeting, email_context)
        return self._call_api_with_retry(system, prompt, email_context)

    def generate_meeting_prep_stream(self,
                                     meeting: Dict[str, Any],
                                     email_context: List[Dict[str, Any]]) -> Generator[str, None, None]:
        """Stream a meeting preparation brief."""
        system, prompt = self._build_meeting_context(meeting, email_context)
        yield from self._stream_tokens_with_retry(system, prompt, email_context)

    def is_available(self) -> bool:
        """Check if llama.cpp server is running."""
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=5.0)
            return r.status_code == 200
        except:
            return False

    def list_models(self) -> List[str]:
        """Get available models."""
        try:
            r = httpx.get(f"{self.base_url}/v1/models", timeout=10.0)
            if r.status_code == 200:
                return [m["id"] for m in r.json().get("data", [])]
        except:
            pass
        return []


class ClaudeClient(BaseLLMClient):
    """Claude API client using the Anthropic SDK."""

    def __init__(self,
                 api_key: str,
                 model: str = "claude-haiku-4-5-20251001",
                 temperature: float = 0.3):
        super().__init__(temperature)
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = 4096

    def set_model(self, model: str):
        """Update model at runtime."""
        self.model = model
        logger.info(f"Claude model updated to {self.model}")

    def chat(self,
             user_message: str,
             email_context: Optional[List[Dict[str, Any]]] = None) -> str:
        """Send message, get response via Claude API."""
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        prompt = self._build_prompt(user_message, email_context)
        logger.debug(f"Claude prompt length: {len(prompt)} chars")

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def chat_stream(self,
                    user_message: str,
                    email_context: Optional[List[Dict[str, Any]]] = None) -> Generator[str, None, None]:
        """Stream response chunks via Claude API."""
        system = self.SYSTEM_PROMPT.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        prompt = self._build_prompt(user_message, email_context)

        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield text

    def generate_meeting_prep(self,
                              meeting: Dict[str, Any],
                              email_context: List[Dict[str, Any]]) -> str:
        """Generate a meeting preparation brief via Claude API."""
        system, prompt = self._build_meeting_context(meeting, email_context)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def generate_meeting_prep_stream(self,
                                     meeting: Dict[str, Any],
                                     email_context: List[Dict[str, Any]]) -> Generator[str, None, None]:
        """Stream a meeting preparation brief via Claude API."""
        system, prompt = self._build_meeting_context(meeting, email_context)

        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield text

    def is_available(self) -> bool:
        """Check if Claude API is accessible."""
        try:
            # Quick test with minimal tokens
            response = self.client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}]
            )
            return True
        except Exception as e:
            logger.warning(f"Claude API not available: {e}")
            return False

    def list_models(self) -> List[str]:
        """Return available Claude models."""
        return [self.model]


# Client management
_local_client: Optional[LlamaCppClient] = None
_claude_client: Optional[ClaudeClient] = None


def get_llm_client(backend: str = "local") -> BaseLLMClient:
    """Get LLM client by backend type.

    Args:
        backend: "local" for llama.cpp, "claude" for Claude API
    """
    if backend == "claude":
        return _get_claude_client()
    return _get_local_client()


def _get_local_client() -> LlamaCppClient:
    global _local_client
    if _local_client is None:
        import config
        _local_client = LlamaCppClient(
            base_url=config.LLAMACPP_URL,
            model=config.OLLAMA_MODEL,
            temperature=config.LLM_TEMPERATURE
        )
    return _local_client


def _get_claude_client() -> ClaudeClient:
    global _claude_client
    if _claude_client is None:
        import config
        if not config.CLAUDE_API_KEY:
            raise ValueError("CLAUDE_API_KEY not set in .env — cannot use Claude backend")
        _claude_client = ClaudeClient(
            api_key=config.CLAUDE_API_KEY,
            model=config.CLAUDE_MODEL,
            temperature=config.LLM_TEMPERATURE
        )
    return _claude_client


# Backward-compatible alias
def get_ollama_client() -> LlamaCppClient:
    return _get_local_client()
