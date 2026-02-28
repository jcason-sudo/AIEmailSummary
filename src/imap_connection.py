"""
IMAP email connection module.
Supports Gmail, Yahoo, and any standard IMAP server.
"""

import imaplib
import email
import email.header
import email.utils
import logging
from datetime import datetime, timedelta
from typing import Generator, Optional, List, Dict
import html2text

from models import EmailMessage, EmailDirection

logger = logging.getLogger(__name__)


# Well-known IMAP servers
IMAP_SERVERS = {
    'gmail': {'host': 'imap.gmail.com', 'port': 993},
    'yahoo': {'host': 'imap.mail.yahoo.com', 'port': 993},
    'outlook': {'host': 'outlook.office365.com', 'port': 993},
    'hotmail': {'host': 'outlook.office365.com', 'port': 993},
}

# Folders to skip (trash, spam, drafts are noise)
SKIP_FOLDERS = {
    'trash', 'deleted', 'deleted items', 'deleted messages',
    'spam', 'junk', 'bulk',
    'drafts', 'draft',
    '[gmail]/trash', '[gmail]/spam', '[gmail]/drafts',
    '[gmail]/all mail',  # skip — already covered by individual folders
}


def _decode_header(header_value: str) -> str:
    """Decode an email header value (handles encoded words)."""
    if not header_value:
        return ""
    decoded_parts = email.header.decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ' '.join(result)


def _parse_address(addr_str: str) -> tuple:
    """Parse an email address into (name, email)."""
    if not addr_str:
        return "", ""
    name, address = email.utils.parseaddr(addr_str)
    return _decode_header(name), address


def _parse_address_list(header_value: str) -> List[str]:
    """Parse a comma-separated list of addresses into email strings."""
    if not header_value:
        return []
    addresses = email.utils.getaddresses([header_value])
    return [addr for _, addr in addresses if addr]


def _parse_icalendar(ical_text: str) -> str:
    """Extract readable info from iCalendar (text/calendar) data."""
    lines = ical_text.splitlines()
    info = {}
    description_lines = []
    in_description = False

    for line in lines:
        if in_description:
            if line.startswith(' ') or line.startswith('\t'):
                description_lines.append(line.strip())
                continue
            else:
                in_description = False
                info['description'] = ' '.join(description_lines)

        if line.startswith('SUMMARY:'):
            info['summary'] = line[8:].strip()
        elif line.startswith('DTSTART'):
            val = line.split(':', 1)[-1].strip()
            info['start'] = val
        elif line.startswith('DTEND'):
            val = line.split(':', 1)[-1].strip()
            info['end'] = val
        elif line.startswith('LOCATION:'):
            info['location'] = line[9:].strip()
        elif line.startswith('ORGANIZER'):
            val = line.split(':', 1)[-1].strip().replace('mailto:', '')
            info['organizer'] = val
        elif line.startswith('ATTENDEE'):
            val = line.split(':', 1)[-1].strip().replace('mailto:', '')
            info.setdefault('attendees', []).append(val)
        elif line.startswith('DESCRIPTION:'):
            in_description = True
            description_lines = [line[12:].strip()]

    if not info:
        return ""

    parts = ["[Calendar Event]"]
    if 'summary' in info:
        parts.append(f"Event: {info['summary']}")
    if 'start' in info:
        parts.append(f"Start: {info['start']}")
    if 'end' in info:
        parts.append(f"End: {info['end']}")
    if 'location' in info:
        parts.append(f"Location: {info['location']}")
    if 'organizer' in info:
        parts.append(f"Organizer: {info['organizer']}")
    if 'attendees' in info:
        parts.append(f"Attendees: {', '.join(info['attendees'][:10])}")
    if 'description' in info:
        parts.append(f"Details: {info['description'][:500]}")
    return '\n'.join(parts)


def _get_body(msg) -> tuple:
    """Extract plain text, HTML body, and calendar data from an email message."""
    body_text = ""
    body_html = ""
    calendar_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get('Content-Disposition', ''))
            if 'attachment' in disposition:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                if content_type == 'text/plain' and not body_text:
                    body_text = text
                elif content_type == 'text/html' and not body_html:
                    body_html = text
                elif content_type == 'text/calendar' and not calendar_text:
                    calendar_text = _parse_icalendar(text)
            except Exception:
                continue
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                if content_type == 'text/plain':
                    body_text = text
                elif content_type == 'text/html':
                    body_html = text
                elif content_type == 'text/calendar':
                    calendar_text = _parse_icalendar(text)
        except Exception:
            pass

    # Convert HTML to text if no plain text
    if not body_text and body_html:
        try:
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.ignore_images = True
            converter.body_width = 0
            body_text = converter.handle(body_html)
        except Exception:
            body_text = body_html

    # Append calendar info to body so it's searchable
    if calendar_text:
        if body_text:
            body_text = body_text + "\n\n" + calendar_text
        else:
            body_text = calendar_text

    return body_text, body_html


class IMAPConnection:
    """Connect to an IMAP email server (Gmail, Yahoo, etc.)."""

    def __init__(self, host: str, port: int, username: str, password: str,
                 provider: str = 'default', label: str = ''):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.provider = provider
        self.label = label or provider
        self.conn = None

    def connect(self) -> bool:
        try:
            self.conn = imaplib.IMAP4_SSL(self.host, self.port)
            self.conn.login(self.username, self.password)
            logger.info(f"Connected to {self.label} IMAP ({self.host})")
            return True
        except Exception as e:
            logger.error(f"IMAP connection failed for {self.label}: {e}")
            return False

    def disconnect(self):
        if self.conn:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def list_folders(self) -> List[str]:
        """List all available folders including subfolders."""
        if not self.conn:
            return []
        try:
            status, folder_data = self.conn.list()
            if status != 'OK':
                return []
            folders = []
            for item in folder_data:
                if isinstance(item, bytes):
                    decoded = item.decode('utf-8', errors='replace')
                    # IMAP LIST format: (\Flags) "delimiter" "folder name"
                    # Find the last quoted string or unquoted name
                    import re
                    match = re.search(r'"([^"]*)"$', decoded)
                    if match:
                        folders.append(match.group(1))
                    else:
                        name = decoded.rsplit(' ', 1)[-1].strip('"')
                        folders.append(name)
            return folders
        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
            return []

    def get_all_folders(self) -> List[str]:
        """Get all folders worth ingesting (skips trash, spam, drafts)."""
        all_folders = self.list_folders()
        result = []
        for f in all_folders:
            if f.lower() in SKIP_FOLDERS:
                continue
            # Also skip by partial match for provider-specific naming
            lower = f.lower()
            if any(skip in lower for skip in ['trash', 'spam', 'junk', 'bulk']):
                continue
            # Skip IMAP internal folders
            if lower.startswith('[gmail]/') and lower == '[gmail]':
                continue
            result.append(f)
        logger.info(f"Discovered {len(result)} folders for {self.label}: {result}")
        return result

    def _is_sent_folder(self, folder_name: str) -> bool:
        """Check if folder is a sent folder."""
        lower = folder_name.lower()
        return any(s in lower for s in ['sent', 'envoy'])

    def _parse_imap_message(self, msg, folder_name: str) -> Optional[EmailMessage]:
        """Parse a raw email message into an EmailMessage."""
        try:
            # Message-ID
            message_id = msg.get('Message-ID', '') or ''
            message_id = message_id.strip('<>')

            # References / In-Reply-To for threading
            in_reply_to = msg.get('In-Reply-To', '') or ''
            in_reply_to = in_reply_to.strip('<>')
            references_raw = msg.get('References', '') or ''
            references = [r.strip('<>') for r in references_raw.split() if r.strip()]

            # Conversation ID — use first reference (root message) or in-reply-to
            conversation_id = ''
            if references:
                conversation_id = references[0]
            elif in_reply_to:
                conversation_id = in_reply_to
            elif message_id:
                conversation_id = message_id

            # Sender
            sender_name, sender_email = _parse_address(msg.get('From', ''))

            # Subject
            subject = _decode_header(msg.get('Subject', ''))

            # Date
            date_str = msg.get('Date', '')
            sent_date = None
            if date_str:
                try:
                    parsed = email.utils.parsedate_to_datetime(date_str)
                    sent_date = parsed.replace(tzinfo=None)
                except Exception:
                    pass

            # Recipients
            to_list = _parse_address_list(_decode_header(msg.get('To', '')))
            cc_list = _parse_address_list(_decode_header(msg.get('Cc', '')))
            bcc_list = _parse_address_list(_decode_header(msg.get('Bcc', '')))

            # Body
            body_text, body_html = _get_body(msg)

            # Direction
            if self._is_sent_folder(folder_name):
                direction = EmailDirection.SENT
            elif sender_email.lower() == self.username.lower():
                direction = EmailDirection.SENT
            else:
                direction = EmailDirection.RECEIVED

            # Attachments
            has_attachments = False
            if msg.is_multipart():
                for part in msg.walk():
                    disposition = str(part.get('Content-Disposition', ''))
                    if 'attachment' in disposition:
                        has_attachments = True
                        break

            # Importance
            importance = 'normal'
            x_priority = msg.get('X-Priority', '')
            if x_priority:
                try:
                    pri = int(x_priority.strip()[0])
                    if pri <= 2:
                        importance = 'high'
                    elif pri >= 4:
                        importance = 'low'
                except Exception:
                    pass

            return EmailMessage(
                message_id=message_id,
                conversation_id=conversation_id,
                sender=sender_email,
                sender_name=sender_name,
                recipients_to=to_list,
                recipients_cc=cc_list,
                recipients_bcc=bcc_list,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                sent_date=sent_date,
                received_date=sent_date,  # IMAP doesn't separate these cleanly
                folder=folder_name,
                source=self.label,
                direction=direction,
                is_read=True,  # IMAP fetch marks as read by default
                in_reply_to=in_reply_to or None,
                references=references,
                has_attachments=has_attachments,
                importance=importance,
            )

        except Exception as e:
            logger.debug(f"Failed to parse IMAP message: {e}")
            return None

    def get_emails(self,
                   folders: Optional[List[str]] = None,
                   since: Optional[datetime] = None,
                   limit: Optional[int] = None) -> Generator[EmailMessage, None, None]:
        """
        Fetch emails from IMAP server.

        Args:
            folders: List of folder names. If None, uses provider defaults.
            since: Only fetch emails after this date.
            limit: Max emails per folder.

        Yields:
            EmailMessage objects
        """
        if not self.conn:
            logger.error("Not connected to IMAP server")
            return

        if folders is None:
            folders = self.get_all_folders()

        for folder_name in folders:
            try:
                status, _ = self.conn.select(f'"{folder_name}"', readonly=True)
                if status != 'OK':
                    logger.warning(f"Could not select folder: {folder_name}")
                    continue
            except Exception as e:
                logger.warning(f"Failed to select folder {folder_name}: {e}")
                continue

            logger.info(f"Processing IMAP folder: {folder_name} ({self.label})")

            # Build search criteria
            search_criteria = 'ALL'
            if since:
                date_str = since.strftime('%d-%b-%Y')
                search_criteria = f'(SINCE {date_str})'

            try:
                status, msg_ids = self.conn.search(None, search_criteria)
                if status != 'OK':
                    continue

                id_list = msg_ids[0].split()
                if not id_list:
                    logger.info(f"No emails in {folder_name}")
                    continue

                # Process newest first
                id_list = list(reversed(id_list))
                if limit:
                    id_list = id_list[:limit]

                logger.info(f"Fetching {len(id_list)} emails from {folder_name}")

                count = 0
                for msg_id in id_list:
                    try:
                        # PEEK avoids marking as read
                        status, data = self.conn.fetch(msg_id, '(BODY.PEEK[])')
                        if status != 'OK' or not data or not data[0]:
                            continue

                        raw_email = data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        parsed = self._parse_imap_message(msg, folder_name)
                        if parsed:
                            yield parsed
                            count += 1

                    except Exception as e:
                        logger.debug(f"Failed to fetch message {msg_id}: {e}")
                        continue

                logger.info(f"Fetched {count} emails from {folder_name}")

            except Exception as e:
                logger.error(f"Error searching folder {folder_name}: {e}")
                continue


def parse_imap_accounts(config_str: str) -> List[Dict]:
    """
    Parse IMAP accounts from config string.
    Format: provider:user:password;provider:user:password
    """
    if not config_str or not config_str.strip():
        return []

    accounts = []
    for entry in config_str.split(';'):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(':', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid IMAP account format: {entry[:20]}...")
            continue
        provider, username, password = parts
        provider = provider.strip().lower()
        server = IMAP_SERVERS.get(provider)
        if not server:
            logger.warning(f"Unknown IMAP provider: {provider}. Use host directly.")
            continue
        accounts.append({
            'provider': provider,
            'host': server['host'],
            'port': server['port'],
            'username': username.strip(),
            'password': password.strip(),
        })
    return accounts


def get_imap_emails(accounts: List[Dict],
                    folders: Optional[List[str]] = None,
                    since: Optional[datetime] = None) -> Generator[EmailMessage, None, None]:
    """
    Convenience function to fetch emails from multiple IMAP accounts.

    Args:
        accounts: List of account dicts with host, port, username, password, provider.
        folders: Override default folders.
        since: Date filter.

    Yields:
        EmailMessage objects
    """
    for acct in accounts:
        conn = IMAPConnection(
            host=acct['host'],
            port=acct['port'],
            username=acct['username'],
            password=acct['password'],
            provider=acct.get('provider', 'default'),
            label=acct.get('provider', acct['host']),
        )
        with conn:
            yield from conn.get_emails(folders=folders, since=since)
