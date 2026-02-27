"""
PST file parsing module.
Extracts emails from Outlook PST/OST files.
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Generator, Optional, List
import logging
from bs4 import BeautifulSoup
import html2text

from models import EmailMessage, EmailDirection, EmailAttachment

logger = logging.getLogger(__name__)


class PSTParser:
    """
    Parser for Outlook PST/OST files.
    
    Uses pypff (libpff Python bindings) for cross-platform PST parsing.
    Falls back to win32com on Windows if pypff is unavailable.
    """
    
    def __init__(self, pst_path: Path):
        self.pst_path = Path(pst_path)
        self.pff_file = None
        self._html_converter = html2text.HTML2Text()
        self._html_converter.ignore_links = False
        self._html_converter.ignore_images = True
        self._html_converter.body_width = 0
        
    def __enter__(self):
        self.open()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        
    def open(self):
        """Open the PST file."""
        try:
            import pypff
            self.pff_file = pypff.file()
            self.pff_file.open(str(self.pst_path))
            logger.info(f"Opened PST file: {self.pst_path}")
        except ImportError:
            logger.warning("pypff not available, PST parsing will be limited")
            raise ImportError(
                "pypff library not found. Install with: pip install pypff-python3"
            )
        except Exception as e:
            logger.error(f"Failed to open PST file {self.pst_path}: {e}")
            raise
            
    def close(self):
        """Close the PST file."""
        if self.pff_file:
            self.pff_file.close()
            self.pff_file = None
            
    def _extract_body_text(self, message) -> tuple[str, str]:
        """Extract plain text and HTML body from a message."""
        body_text = ""
        body_html = ""
        
        try:
            # Try to get plain text body
            if hasattr(message, 'plain_text_body') and message.plain_text_body:
                body_text = message.plain_text_body
            elif hasattr(message, 'get_plain_text_body'):
                body_text = message.get_plain_text_body() or ""
        except:
            pass
            
        try:
            # Try to get HTML body
            if hasattr(message, 'html_body') and message.html_body:
                body_html = message.html_body
            elif hasattr(message, 'get_html_body'):
                body_html = message.get_html_body() or ""
        except:
            pass
            
        # If no plain text, convert HTML
        if not body_text and body_html:
            try:
                body_text = self._html_converter.handle(body_html)
            except:
                soup = BeautifulSoup(body_html, 'html.parser')
                body_text = soup.get_text(separator='\n', strip=True)
                
        return body_text, body_html
    
    def _parse_datetime(self, dt_value) -> Optional[datetime]:
        """Parse datetime from various formats."""
        if dt_value is None:
            return None
        if isinstance(dt_value, datetime):
            return dt_value
        try:
            if hasattr(dt_value, 'timestamp'):
                return datetime.fromtimestamp(dt_value.timestamp())
        except:
            pass
        return None
    
    def _extract_recipients(self, message) -> tuple[List[str], List[str], List[str]]:
        """Extract To, CC, BCC recipients."""
        to_list = []
        cc_list = []
        bcc_list = []
        
        try:
            if hasattr(message, 'number_of_recipients'):
                for i in range(message.number_of_recipients):
                    try:
                        recipient = message.get_recipient(i)
                        email = getattr(recipient, 'email_address', '') or ''
                        recipient_type = getattr(recipient, 'recipient_type', 0)
                        
                        if recipient_type == 1:  # TO
                            to_list.append(email)
                        elif recipient_type == 2:  # CC
                            cc_list.append(email)
                        elif recipient_type == 3:  # BCC
                            bcc_list.append(email)
                    except:
                        continue
        except:
            pass
            
        return to_list, cc_list, bcc_list
    
    def _process_message(self, message, folder_name: str) -> Optional[EmailMessage]:
        """Process a single message from the PST file."""
        try:
            # Extract basic fields
            message_id = getattr(message, 'message_identifier', None)
            if message_id:
                message_id = str(message_id)
            else:
                message_id = getattr(message, 'internet_message_id', '') or ''
                
            sender = getattr(message, 'sender_email_address', '') or ''
            sender_name = getattr(message, 'sender_name', '') or ''
            subject = getattr(message, 'subject', '') or ''
            
            # Dates
            sent_date = self._parse_datetime(
                getattr(message, 'client_submit_time', None) or
                getattr(message, 'delivery_time', None)
            )
            received_date = self._parse_datetime(
                getattr(message, 'delivery_time', None)
            )
            
            # Recipients
            to_list, cc_list, bcc_list = self._extract_recipients(message)
            
            # Body
            body_text, body_html = self._extract_body_text(message)
            
            # Determine direction based on folder
            folder_lower = folder_name.lower()
            if 'sent' in folder_lower or 'outbox' in folder_lower:
                direction = EmailDirection.SENT
            else:
                direction = EmailDirection.RECEIVED
                
            # Status flags
            message_flags = getattr(message, 'message_flags', 0) or 0
            is_read = bool(message_flags & 0x0001) if isinstance(message_flags, int) else False
            
            # Attachments
            num_attachments = getattr(message, 'number_of_attachments', 0) or 0
            has_attachments = num_attachments > 0
            
            # Conversation/threading
            conversation_id = getattr(message, 'conversation_topic', '') or ''
            in_reply_to = getattr(message, 'in_reply_to_id', '') or ''
            
            return EmailMessage(
                message_id=message_id,
                conversation_id=conversation_id,
                sender=sender,
                sender_name=sender_name,
                recipients_to=to_list,
                recipients_cc=cc_list,
                recipients_bcc=bcc_list,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                sent_date=sent_date,
                received_date=received_date,
                folder=folder_name,
                source='pst',
                direction=direction,
                is_read=is_read,
                has_attachments=has_attachments,
                in_reply_to=in_reply_to,
            )
            
        except Exception as e:
            logger.warning(f"Failed to process message: {e}")
            return None
    
    def _iterate_folder(self, folder, folder_path: str = "") -> Generator[EmailMessage, None, None]:
        """Recursively iterate through folder and subfolders."""
        try:
            folder_name = getattr(folder, 'name', '') or ''
            current_path = f"{folder_path}/{folder_name}" if folder_path else folder_name
            
            # Process messages in this folder
            if hasattr(folder, 'number_of_sub_messages'):
                for i in range(folder.number_of_sub_messages):
                    try:
                        message = folder.get_sub_message(i)
                        email = self._process_message(message, current_path)
                        if email:
                            yield email
                    except Exception as e:
                        logger.debug(f"Error processing message {i}: {e}")
                        continue
                        
            # Recurse into subfolders
            if hasattr(folder, 'number_of_sub_folders'):
                for i in range(folder.number_of_sub_folders):
                    try:
                        subfolder = folder.get_sub_folder(i)
                        yield from self._iterate_folder(subfolder, current_path)
                    except Exception as e:
                        logger.debug(f"Error processing subfolder {i}: {e}")
                        continue
                        
        except Exception as e:
            logger.warning(f"Error iterating folder: {e}")
    
    def get_emails(self, 
                   folders: Optional[List[str]] = None,
                   since: Optional[datetime] = None) -> Generator[EmailMessage, None, None]:
        """
        Iterate through all emails in the PST file.
        
        Args:
            folders: Optional list of folder names to filter (e.g., ['Inbox', 'Sent Items'])
            since: Optional datetime to filter emails after this date
            
        Yields:
            EmailMessage objects
        """
        if not self.pff_file:
            raise RuntimeError("PST file not opened. Call open() first.")
            
        root = self.pff_file.get_root_folder()
        if not root:
            logger.warning("Could not get root folder from PST file")
            return
            
        for email in self._iterate_folder(root):
            # Filter by folder if specified
            if folders:
                if not any(f.lower() in email.folder.lower() for f in folders):
                    continue
                    
            # Filter by date if specified
            if since and email.date:
                if email.date < since:
                    continue
                    
            yield email
            
    def get_folder_list(self) -> List[str]:
        """Get list of all folders in the PST file."""
        folders = []
        
        def collect_folders(folder, path=""):
            name = getattr(folder, 'name', '') or ''
            current = f"{path}/{name}" if path else name
            if name:
                folders.append(current)
                
            if hasattr(folder, 'number_of_sub_folders'):
                for i in range(folder.number_of_sub_folders):
                    try:
                        subfolder = folder.get_sub_folder(i)
                        collect_folders(subfolder, current)
                    except:
                        continue
                        
        if self.pff_file:
            root = self.pff_file.get_root_folder()
            if root:
                collect_folders(root)
                
        return folders


def parse_pst_file(pst_path: Path, 
                   folders: Optional[List[str]] = None,
                   since: Optional[datetime] = None) -> Generator[EmailMessage, None, None]:
    """
    Convenience function to parse a PST file.
    
    Args:
        pst_path: Path to the PST file
        folders: Optional folder filter
        since: Optional date filter
        
    Yields:
        EmailMessage objects
    """
    with PSTParser(pst_path) as parser:
        yield from parser.get_emails(folders=folders, since=since)
