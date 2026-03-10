"""
Data models for email processing.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
import hashlib
import json


class EmailDirection(Enum):
    """Direction of email - sent or received."""
    SENT = "sent"
    RECEIVED = "received"
    

class EmailStatus(Enum):
    """Status indicators for email."""
    UNREAD = "unread"
    READ = "read"
    REPLIED = "replied"
    FORWARDED = "forwarded"
    FLAGGED = "flagged"


@dataclass
class EmailAttachment:
    """Represents an email attachment."""
    filename: str
    size_bytes: int
    content_type: str
    content: Optional[bytes] = None


@dataclass
class EmailMessage:
    """Represents a single email message."""
    
    # Core identifiers
    message_id: str
    conversation_id: Optional[str] = None
    
    # Participants
    sender: str = ""
    sender_name: str = ""
    recipients_to: List[str] = field(default_factory=list)
    recipients_cc: List[str] = field(default_factory=list)
    recipients_bcc: List[str] = field(default_factory=list)
    
    # Content
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    
    # Metadata
    sent_date: Optional[datetime] = None
    received_date: Optional[datetime] = None
    folder: str = ""
    source: str = ""  # 'pst' or 'outlook'
    
    # Status
    direction: EmailDirection = EmailDirection.RECEIVED
    is_read: bool = False
    is_replied: bool = False
    is_forwarded: bool = False
    is_flagged: bool = False
    importance: str = "normal"
    email_type: str = ""  # "meeting_note" for Zoom/bot emails, "" for normal

    # Thread info
    in_reply_to: Optional[str] = None
    references: List[str] = field(default_factory=list)
    
    # Attachments
    attachments: List[EmailAttachment] = field(default_factory=list)
    has_attachments: bool = False
    
    @property
    def unique_id(self) -> str:
        """Generate a unique ID for this email."""
        if self.message_id:
            return hashlib.sha256(self.message_id.encode()).hexdigest()[:32]
        # Fallback: hash key fields
        key = f"{self.sender}:{self.subject}:{self.sent_date}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]
    
    @property
    def date(self) -> Optional[datetime]:
        """Get the relevant date for this email."""
        return self.sent_date or self.received_date
    
    @property
    def all_recipients(self) -> List[str]:
        """Get all recipients."""
        return self.recipients_to + self.recipients_cc + self.recipients_bcc
    
    def to_document(self) -> str:
        """Convert to a searchable document string."""
        parts = []

        if self.email_type == "meeting_note":
            parts.append("[Meeting Summary]")

        # Add metadata
        if self.sender:
            parts.append(f"From: {self.sender_name or self.sender}")
        if self.recipients_to:
            parts.append(f"To: {', '.join(self.recipients_to)}")
        if self.subject:
            parts.append(f"Subject: {self.subject}")
        if self.date:
            parts.append(f"Date: {self.date.strftime('%Y-%m-%d %H:%M')}")
        if self.folder:
            parts.append(f"Folder: {self.folder}")

        parts.append("")  # Blank line

        # Add body
        body = self.body_text or ""
        if body:
            # Truncate very long bodies
            if len(body) > 5000:
                body = body[:5000] + "..."
            parts.append(body)

        # Add extracted attachment text
        if self.attachments:
            from attachment_extractor import extract_text
            for att in self.attachments:
                if att.content:
                    text = extract_text(att.filename, att.content, att.content_type)
                    if text:
                        parts.append(f"\n[Attachment: {att.filename}]")
                        parts.append(text[:3000])

        return "\n".join(parts)
    
    def to_metadata(self) -> dict:
        """Convert to metadata dict for vector store."""
        return {
            "message_id": self.message_id or "",
            "conversation_id": self.conversation_id or "",
            "sender": self.sender,
            "sender_name": self.sender_name,
            "recipients": ",".join(self.recipients_to[:5]),  # Limit for storage
            "subject": self.subject[:500] if self.subject else "",
            "date": self.date.isoformat() if self.date else "",
            "folder": self.folder,
            "source": self.source,
            "direction": self.direction.value,
            "is_read": self.is_read,
            "is_replied": self.is_replied,
            "is_flagged": self.is_flagged,
            "has_attachments": self.has_attachments,
            "importance": self.importance,
            "email_type": self.email_type,
            "attachment_names": ",".join(a.filename for a in self.attachments[:10] if a.filename),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'EmailMessage':
        """Create from dictionary."""
        # Handle enum fields
        if 'direction' in data and isinstance(data['direction'], str):
            data['direction'] = EmailDirection(data['direction'])
            
        # Handle datetime fields
        for date_field in ['sent_date', 'received_date']:
            if date_field in data and isinstance(data[date_field], str):
                try:
                    data[date_field] = datetime.fromisoformat(data[date_field])
                except:
                    data[date_field] = None
                    
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EmailThread:
    """Represents a conversation thread of emails."""
    
    conversation_id: str
    subject: str
    messages: List[EmailMessage] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)
    
    @property
    def latest_date(self) -> Optional[datetime]:
        """Get the date of the most recent message."""
        if not self.messages:
            return None
        return max((m.date for m in self.messages if m.date), default=None)
    
    @property
    def is_awaiting_response(self) -> bool:
        """Check if thread is awaiting a response from others."""
        if not self.messages:
            return False
        # If my last message is the most recent and no reply received
        latest = sorted(self.messages, key=lambda m: m.date or datetime.min)[-1]
        return latest.direction == EmailDirection.SENT and not latest.is_replied
    
    @property
    def needs_action(self) -> bool:
        """Check if thread needs action from me."""
        if not self.messages:
            return False
        latest = sorted(self.messages, key=lambda m: m.date or datetime.min)[-1]
        return (
            latest.direction == EmailDirection.RECEIVED 
            and not latest.is_replied 
            and not latest.is_read
        )
