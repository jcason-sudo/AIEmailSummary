"""
Fact card extraction using Claude Haiku.

Processes emails through Claude to extract structured knowledge:
entities, intents, commitments, action items, sentiment, topics.
"""

import json
import logging
from typing import List, Dict, Any, Optional

from fact_cards import FactCard
from fact_store import get_fact_store

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract structured information from this email. Return ONLY valid JSON with these fields:

{
  "entities": ["list of people, companies, projects, products mentioned"],
  "intents": ["list from: request, approval, fyi, question, escalation, follow_up, scheduling, introduction"],
  "commitments": [{"who": "person name", "what": "what they committed to", "by_when": "deadline if mentioned"}],
  "action_items": [{"description": "what needs to be done", "assignee": "who should do it", "deadline": "when"}],
  "key_facts": ["important factual statements from the email"],
  "sentiment": "one of: neutral, urgent, positive, negative",
  "topics": ["topic tags for this email"]
}

Rules:
- Only extract information explicitly stated in the email
- For commitments, only include clear promises/agreements, not vague statements
- For action items, only include explicit tasks, not implied ones
- Keep entities as short names (e.g., "John" not "John said in his email")
- If a field has no data, use an empty list [] or "neutral" for sentiment
- Return ONLY the JSON object, no markdown formatting or explanation

EMAIL:
From: {sender}
Subject: {subject}
Date: {date}

{body}"""

BATCH_EXTRACTION_PROMPT = """Extract structured information from each of these emails. Return a JSON array where each element corresponds to one email.

Each element should have:
{
  "email_index": 0,
  "entities": ["people, companies, projects mentioned"],
  "intents": ["request/approval/fyi/question/escalation/follow_up/scheduling/introduction"],
  "commitments": [{"who": "", "what": "", "by_when": ""}],
  "action_items": [{"description": "", "assignee": "", "deadline": ""}],
  "key_facts": ["important facts"],
  "sentiment": "neutral/urgent/positive/negative",
  "topics": ["topic tags"]
}

Rules:
- Only extract explicitly stated information
- Return ONLY the JSON array, no markdown
- Keep entities as short names

EMAILS:
{emails}"""


class FactExtractor:
    """Extracts fact cards from emails using Claude Haiku."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-load Claude client."""
        if self._client is None:
            import config
            if not config.CLAUDE_API_KEY:
                raise ValueError("CLAUDE_API_KEY required for fact extraction")
            import anthropic
            self._client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
        return self._client

    def extract_single(self, email_id: str, document: str, metadata: dict) -> Optional[FactCard]:
        """Extract a fact card from a single email."""
        sender = metadata.get('sender_name') or metadata.get('sender', 'Unknown')
        subject = metadata.get('subject', 'No Subject')
        date = metadata.get('date', '')
        body = document[:3000]  # Truncate for API efficiency

        prompt = EXTRACTION_PROMPT.format(
            sender=sender, subject=subject, date=date, body=body
        )

        try:
            client = self._get_client()
            import config
            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1024,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()

            # Parse JSON, stripping markdown fences if present
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                if text.endswith('```'):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)
            return FactCard(
                email_id=email_id,
                entities=data.get('entities', []),
                intents=data.get('intents', []),
                commitments=data.get('commitments', []),
                action_items=data.get('action_items', []),
                key_facts=data.get('key_facts', []),
                sentiment=data.get('sentiment', 'neutral'),
                topics=data.get('topics', []),
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse extraction JSON for {email_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Extraction failed for {email_id}: {e}")
            return None

    def extract_batch(self, emails: List[Dict[str, Any]], batch_size: int = 5) -> List[FactCard]:
        """Extract fact cards from a batch of emails.

        Args:
            emails: List of dicts with 'id', 'document', 'metadata' keys.
            batch_size: Number of emails per API call.

        Returns:
            List of successfully extracted FactCards.
        """
        cards = []

        for i in range(0, len(emails), batch_size):
            batch = emails[i:i + batch_size]

            # Format emails for batch prompt
            email_texts = []
            for idx, email in enumerate(batch):
                meta = email.get('metadata', {})
                sender = meta.get('sender_name') or meta.get('sender', 'Unknown')
                subject = meta.get('subject', 'No Subject')
                date = meta.get('date', '')
                body = email.get('document', '')[:2000]

                email_texts.append(
                    f"--- Email {idx} ---\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date}\n\n"
                    f"{body}\n"
                )

            prompt = BATCH_EXTRACTION_PROMPT.format(
                emails="\n".join(email_texts)
            )

            try:
                client = self._get_client()
                import config
                response = client.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=2048,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}]
                )
                text = response.content[0].text.strip()

                # Strip markdown fences
                if text.startswith('```'):
                    text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                    if text.endswith('```'):
                        text = text[:-3]
                    text = text.strip()

                results = json.loads(text)
                if not isinstance(results, list):
                    results = [results]

                for result in results:
                    idx = result.get('email_index', 0)
                    if 0 <= idx < len(batch):
                        email = batch[idx]
                        card = FactCard(
                            email_id=email.get('id', ''),
                            entities=result.get('entities', []),
                            intents=result.get('intents', []),
                            commitments=result.get('commitments', []),
                            action_items=result.get('action_items', []),
                            key_facts=result.get('key_facts', []),
                            sentiment=result.get('sentiment', 'neutral'),
                            topics=result.get('topics', []),
                        )
                        cards.append(card)

                logger.info(f"Batch {i//batch_size + 1}: extracted {len(results)} cards from {len(batch)} emails")

            except json.JSONDecodeError as e:
                logger.warning(f"Batch {i//batch_size + 1}: JSON parse error: {e}")
                # Fall back to single extraction for this batch
                for email in batch:
                    card = self.extract_single(
                        email.get('id', ''),
                        email.get('document', ''),
                        email.get('metadata', {})
                    )
                    if card:
                        cards.append(card)

            except Exception as e:
                logger.error(f"Batch {i//batch_size + 1} extraction failed: {e}")

        return cards


def run_extraction(limit: int = 500) -> dict:
    """Extract fact cards for unprocessed emails.

    Returns stats about the extraction run.
    """
    from vector_store import get_vector_store

    store = get_vector_store()
    fact_store = get_fact_store()

    total = store._collection.count()
    if total == 0:
        return {'status': 'no_emails', 'extracted': 0}

    # Get all email IDs
    results = store._collection.get(
        include=["documents", "metadatas"],
        limit=min(total, limit)
    )

    all_ids = results['ids']
    unextracted = fact_store.get_unextracted_ids(all_ids)

    if not unextracted:
        return {
            'status': 'all_extracted',
            'total': total,
            'extracted': 0,
            'already_done': len(all_ids),
        }

    logger.info(f"Extracting fact cards for {len(unextracted)} unprocessed emails")

    # Build email dicts for extraction
    emails_to_extract = []
    for i in range(len(results['ids'])):
        if results['ids'][i] in set(unextracted):
            emails_to_extract.append({
                'id': results['ids'][i],
                'document': results['documents'][i] if results['documents'] else '',
                'metadata': results['metadatas'][i] if results['metadatas'] else {},
            })

    extractor = FactExtractor()
    cards = extractor.extract_batch(emails_to_extract)

    # Save all cards
    fact_store.save_cards(cards)

    return {
        'status': 'completed',
        'total_emails': total,
        'unprocessed': len(unextracted),
        'extracted': len(cards),
        'fact_store_stats': fact_store.get_stats(),
    }
