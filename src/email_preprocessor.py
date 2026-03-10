"""
Email preprocessing: boilerplate stripping, segmentation, and chunking.

Emails contain a lot of noise that hurts embedding quality:
- Signatures, disclaimers, legal footers
- Quoted reply history (repeated across every message in a thread)
- HTML artifacts, excessive whitespace
- Forwarded message headers

This module strips all of that and segments emails into meaningful chunks:
1. "fresh" content (the newest message only)
2. quoted history (prior messages, deduplicated)
3. thread summary (generated for multi-message threads)
"""

import re
import hashlib
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Boilerplate detection patterns ---

# Signature markers: lines that typically begin a signature block
SIGNATURE_MARKERS = [
    re.compile(r'^--\s*$'),                          # Standard "-- " separator
    re.compile(r'^_{3,}'),                            # _____ underscores
    re.compile(r'^-{3,}'),                            # ----- dashes
    re.compile(r'^Sent from my (?:iPhone|iPad|Galaxy|Android|BlackBerry)', re.I),
    re.compile(r'^Sent from (?:Mail|Outlook|Yahoo) for', re.I),
    re.compile(r'^Get Outlook for', re.I),
    re.compile(r'^Sent from Outlook$', re.I),
    re.compile(r'^Sent via', re.I),
]

# Disclaimer / legal footer patterns
DISCLAIMER_PATTERNS = [
    re.compile(r'(?:^|\n)(?:CONFIDENTIALITY|DISCLAIMER|LEGAL NOTICE|PRIVILEGED)', re.I),
    re.compile(r'This (?:e-?mail|message|communication) (?:and any|is|may) (?:attachments?|intended|contain)', re.I),
    re.compile(r'(?:intended solely|authorized recipient|not intended for)', re.I),
    re.compile(r'(?:may contain (?:confidential|privileged)|if you (?:are not|have received) (?:the intended|this))', re.I),
    re.compile(r'(?:please (?:notify|delete|disregard)|do not (?:copy|distribute|forward|disseminate))', re.I),
    re.compile(r'(?:unsubscribe|opt.out|email preferences|manage.{0,10}subscription)', re.I),
]

# Quote prefixes indicating reply history
QUOTE_PATTERNS = [
    re.compile(r'^>+\s?'),                            # > quoted lines
    re.compile(r'^On .+ wrote:$', re.I),              # "On Mon, Jan 1... wrote:"
    re.compile(r'^-{2,}\s*(?:Original Message|Forwarded message)', re.I),
    re.compile(r'^From:\s+.+$', re.I),                # Forwarded "From:" header block
    re.compile(r'^\*?From:\*?\s', re.I),              # Bold markdown From:
    re.compile(r'^_{5,}\s*$'),                         # _____ separators before quotes
]

# Patterns for "On DATE, PERSON wrote:" across various formats
ON_WROTE_PATTERN = re.compile(
    r'^On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d).{10,120}wrote:\s*$',
    re.I | re.MULTILINE
)

# Forwarded message header block
FWD_HEADER_PATTERN = re.compile(
    r'^-{2,}\s*(?:Forwarded message|Original Message)\s*-{2,}\s*$',
    re.I | re.MULTILINE
)

# Excessive whitespace
MULTI_NEWLINE = re.compile(r'\n{3,}')
MULTI_SPACE = re.compile(r'[ \t]{2,}')

# URL-heavy lines (tracking pixels, unsubscribe links)
URL_LINE_PATTERN = re.compile(r'^https?://\S{50,}$', re.MULTILINE)


@dataclass
class EmailChunk:
    """A single chunk from an email, ready for embedding."""
    chunk_id: str           # unique ID for this chunk
    email_id: str           # parent email unique_id
    chunk_type: str         # "fresh", "quoted", "thread_summary"
    text: str               # cleaned text content
    metadata: dict = field(default_factory=dict)  # inherited from parent email


@dataclass
class SegmentedEmail:
    """An email segmented into its component parts."""
    fresh_content: str      # newest message only (no quotes/signature)
    quoted_content: str     # quoted reply history
    signature: str          # stripped signature
    disclaimer: str         # stripped disclaimer/legal text
    original_length: int
    cleaned_length: int


def strip_boilerplate(text: str) -> Tuple[str, str, str]:
    """Strip signatures and disclaimers from email text.

    Returns (cleaned_body, signature, disclaimer).
    """
    if not text:
        return "", "", ""

    lines = text.split('\n')
    body_lines = []
    signature_lines = []
    disclaimer_lines = []

    in_signature = False
    in_disclaimer = False

    for line in lines:
        stripped = line.strip()

        # Check for disclaimer start
        if not in_disclaimer:
            for pattern in DISCLAIMER_PATTERNS:
                if pattern.search(stripped):
                    in_disclaimer = True
                    break

        if in_disclaimer:
            disclaimer_lines.append(line)
            continue

        # Check for signature start
        if not in_signature:
            for pattern in SIGNATURE_MARKERS:
                if pattern.match(stripped):
                    in_signature = True
                    break

        if in_signature:
            signature_lines.append(line)
            continue

        body_lines.append(line)

    body = '\n'.join(body_lines)
    signature = '\n'.join(signature_lines)
    disclaimer = '\n'.join(disclaimer_lines)

    return body, signature, disclaimer


def segment_email(text: str) -> SegmentedEmail:
    """Segment an email into fresh content vs quoted history.

    Splits at the first "On ... wrote:" or forwarded message boundary.
    """
    if not text:
        return SegmentedEmail("", "", "", "", 0, 0)

    original_length = len(text)

    # Step 1: Strip boilerplate
    body, signature, disclaimer = strip_boilerplate(text)

    # Step 2: Find the boundary between fresh content and quoted history
    fresh = body
    quoted = ""

    # Try "On DATE, PERSON wrote:" pattern
    match = ON_WROTE_PATTERN.search(body)
    if match:
        split_pos = match.start()
        fresh = body[:split_pos].rstrip()
        quoted = body[split_pos:]
    else:
        # Try forwarded message header
        match = FWD_HEADER_PATTERN.search(body)
        if match:
            split_pos = match.start()
            fresh = body[:split_pos].rstrip()
            quoted = body[split_pos:]
        else:
            # Try ">" quoted lines — find the first block of quoted lines
            lines = body.split('\n')
            first_quote_idx = None
            for i, line in enumerate(lines):
                if line.strip().startswith('>'):
                    # Need at least 2 consecutive quoted lines to be real quotes
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith('>'):
                        first_quote_idx = i
                        break

            if first_quote_idx is not None:
                fresh = '\n'.join(lines[:first_quote_idx]).rstrip()
                quoted = '\n'.join(lines[first_quote_idx:])

    # Step 3: Clean up the fresh content
    fresh = clean_text(fresh)
    quoted = clean_text(quoted)

    return SegmentedEmail(
        fresh_content=fresh,
        quoted_content=quoted,
        signature=signature.strip(),
        disclaimer=disclaimer.strip(),
        original_length=original_length,
        cleaned_length=len(fresh),
    )


def clean_text(text: str) -> str:
    """Normalize whitespace and remove noise from text."""
    if not text:
        return ""

    # Remove long tracking URLs on their own line
    text = URL_LINE_PATTERN.sub('', text)

    # Collapse excessive whitespace
    text = MULTI_NEWLINE.sub('\n\n', text)
    text = MULTI_SPACE.sub(' ', text)

    # Strip leading/trailing whitespace per line
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    return text.strip()


def chunk_email(email_id: str, document: str, metadata: dict,
                max_chunk_size: int = 800,
                min_chunk_size: int = 50) -> List[EmailChunk]:
    """Segment and chunk an email for embedding.

    Produces:
    - One "fresh" chunk for the newest message content
    - Optionally one "quoted" chunk for reply history (if substantial)
    - Metadata header is prepended to the fresh chunk for context

    Args:
        email_id: Unique ID for this email.
        document: Full email document text (from to_document()).
        metadata: Email metadata dict.
        max_chunk_size: Target max chars per chunk.
        min_chunk_size: Minimum chars to keep a chunk.

    Returns:
        List of EmailChunk objects.
    """
    chunks = []

    # Parse out the header lines (From/To/Subject/Date) from body
    header, body = _split_header_body(document)
    segmented = segment_email(body)

    # Build fresh chunk: header + fresh content
    fresh_text = header + "\n\n" + segmented.fresh_content if segmented.fresh_content else header
    fresh_text = fresh_text.strip()

    if len(fresh_text) >= min_chunk_size:
        # Split into sub-chunks if too long
        for i, sub in enumerate(_split_into_chunks(fresh_text, max_chunk_size)):
            chunk_suffix = f"_f{i}" if i > 0 else "_fresh"
            chunks.append(EmailChunk(
                chunk_id=f"{email_id}{chunk_suffix}",
                email_id=email_id,
                chunk_type="fresh",
                text=sub,
                metadata={**metadata, 'chunk_type': 'fresh', 'chunk_index': i},
            ))

    # Build quoted chunk (if substantial and different from fresh)
    if segmented.quoted_content and len(segmented.quoted_content) >= min_chunk_size:
        quoted_text = f"Subject: {metadata.get('subject', '')}\n[Quoted history]\n\n{segmented.quoted_content}"
        for i, sub in enumerate(_split_into_chunks(quoted_text, max_chunk_size)):
            chunk_suffix = f"_q{i}" if i > 0 else "_quoted"
            chunks.append(EmailChunk(
                chunk_id=f"{email_id}{chunk_suffix}",
                email_id=email_id,
                chunk_type="quoted",
                text=sub,
                metadata={**metadata, 'chunk_type': 'quoted', 'chunk_index': i},
            ))

    # Build attachment chunks (separate from email body for better retrieval)
    attachment_sections = _extract_attachment_sections(document)
    for att_idx, (att_name, att_text) in enumerate(attachment_sections):
        if len(att_text) < min_chunk_size:
            continue
        for i, sub in enumerate(_split_into_chunks(att_text, max_chunk_size)):
            chunk_suffix = f"_att{att_idx}_{i}" if i > 0 else f"_att{att_idx}"
            chunks.append(EmailChunk(
                chunk_id=f"{email_id}{chunk_suffix}",
                email_id=email_id,
                chunk_type="attachment",
                text=f"Subject: {metadata.get('subject', '')}\n[Attachment: {att_name}]\n\n{sub}",
                metadata={**metadata, 'chunk_type': 'attachment', 'chunk_index': i,
                          'attachment_name': att_name},
            ))

    # Fallback: if no chunks produced, use the whole document (cleaned)
    if not chunks:
        cleaned = clean_text(document)
        if len(cleaned) >= min_chunk_size:
            chunks.append(EmailChunk(
                chunk_id=f"{email_id}_fresh",
                email_id=email_id,
                chunk_type="fresh",
                text=cleaned[:max_chunk_size],
                metadata={**metadata, 'chunk_type': 'fresh', 'chunk_index': 0},
            ))

    return chunks


def generate_thread_summary_chunk(conversation_id: str,
                                   thread_emails: List[Dict],
                                   metadata_template: dict) -> Optional[EmailChunk]:
    """Generate a summary chunk for a conversation thread.

    Combines key metadata from all messages into a compact summary
    that captures the thread's arc without repeating full bodies.
    """
    if not thread_emails or len(thread_emails) < 2:
        return None

    # Sort by date
    sorted_emails = sorted(thread_emails, key=lambda e: e.get('metadata', {}).get('date', ''))

    parts = []
    first = sorted_emails[0].get('metadata', {})
    last = sorted_emails[-1].get('metadata', {})

    subject = last.get('subject', first.get('subject', 'No Subject'))
    parts.append(f"Thread: {subject}")
    parts.append(f"Messages: {len(sorted_emails)}")
    parts.append(f"Period: {first.get('date', '?')[:10]} to {last.get('date', '?')[:10]}")

    # Participants
    participants = set()
    for e in sorted_emails:
        sender = e.get('metadata', {}).get('sender_name') or e.get('metadata', {}).get('sender', '')
        if sender:
            participants.add(sender)
    parts.append(f"Participants: {', '.join(list(participants)[:8])}")

    # Timeline of messages (compact)
    parts.append("\nTimeline:")
    for e in sorted_emails:
        meta = e.get('metadata', {})
        sender = meta.get('sender_name') or meta.get('sender', '?')
        date = meta.get('date', '')[:16]  # YYYY-MM-DDTHH:MM
        direction = 'SENT' if meta.get('direction') == 'sent' else 'RECV'

        # Get first meaningful line of body
        doc = e.get('document', '')
        preview = _get_first_line(doc, max_len=120)
        parts.append(f"  [{date}] {direction} {sender}: {preview}")

    summary_text = '\n'.join(parts)

    # Build metadata from the latest message
    chunk_meta = {**metadata_template}
    chunk_meta['chunk_type'] = 'thread_summary'
    chunk_meta['conversation_id'] = conversation_id
    chunk_meta['message_count'] = len(sorted_emails)

    chunk_id = hashlib.sha256(f"thread_summary_{conversation_id}".encode()).hexdigest()[:32]

    return EmailChunk(
        chunk_id=chunk_id,
        email_id=chunk_id,
        chunk_type="thread_summary",
        text=summary_text[:1200],
        metadata=chunk_meta,
    )


def _split_header_body(document: str) -> Tuple[str, str]:
    """Split the document into header (From/To/Subject/Date/Folder) and body."""
    lines = document.split('\n')
    header_lines = []
    body_start = 0

    for i, line in enumerate(lines):
        if line.strip() == '' and i > 0:
            # Blank line separates header from body
            body_start = i + 1
            break
        if any(line.startswith(prefix) for prefix in ('From:', 'To:', 'Subject:', 'Date:', 'Folder:')):
            header_lines.append(line)
        else:
            # Non-header line before blank = body starts here
            body_start = i
            break

    header = '\n'.join(header_lines)
    body = '\n'.join(lines[body_start:])
    return header, body


def _split_into_chunks(text: str, max_size: int) -> List[str]:
    """Split text into chunks at paragraph boundaries."""
    if len(text) <= max_size:
        return [text]

    chunks = []
    paragraphs = text.split('\n\n')
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > max_size and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks if chunks else [text[:max_size]]


def _extract_attachment_sections(document: str) -> List[Tuple[str, str]]:
    """Extract [Attachment: filename] sections from a document.

    Returns list of (filename, text) tuples.
    """
    sections = []
    marker = '[Attachment: '
    pos = 0
    while True:
        idx = document.find(marker, pos)
        if idx == -1:
            break
        # Parse filename
        end_bracket = document.find(']', idx + len(marker))
        if end_bracket == -1:
            break
        filename = document[idx + len(marker):end_bracket]

        # Text runs from after the "]" line to the next [Attachment: or end
        text_start = document.find('\n', end_bracket)
        if text_start == -1:
            text_start = end_bracket + 1
        else:
            text_start += 1

        next_att = document.find(marker, text_start)
        if next_att == -1:
            text = document[text_start:]
        else:
            text = document[text_start:next_att]

        text = text.strip()
        if text:
            sections.append((filename, text))

        pos = end_bracket + 1

    return sections


def _get_first_line(document: str, max_len: int = 120) -> str:
    """Get first meaningful content line from a document."""
    for line in document.split('\n'):
        stripped = line.strip()
        # Skip header lines and empty lines
        if not stripped:
            continue
        if any(stripped.startswith(p) for p in ('From:', 'To:', 'Subject:', 'Date:', 'Folder:')):
            continue
        if stripped.startswith('>'):
            continue
        return stripped[:max_len]
    return ""
