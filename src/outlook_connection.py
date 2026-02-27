"""
Outlook Desktop connection module.
Connects to the running Outlook application via COM (Windows only).
"""

import sys
import logging
from datetime import datetime, timedelta
from typing import Generator, Optional, List
from bs4 import BeautifulSoup
import html2text

from models import EmailMessage, EmailDirection

logger = logging.getLogger(__name__)

# Check if we're on Windows
IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    try:
        import win32com.client
        import pythoncom
        OUTLOOK_AVAILABLE = True
    except ImportError:
        OUTLOOK_AVAILABLE = False
        logger.warning("pywin32 not installed. Outlook connection unavailable.")
else:
    OUTLOOK_AVAILABLE = False
    logger.info("Not on Windows. Outlook COM connection unavailable.")


class OutlookConnection:
    """
    Connection to the Outlook Desktop application via COM interface.
    Windows only - requires pywin32.
    """
    
    # Outlook folder types
    FOLDER_INBOX = 6
    FOLDER_SENT = 5
    FOLDER_OUTBOX = 4
    FOLDER_DRAFTS = 16
    FOLDER_DELETED = 3
    FOLDER_JUNK = 23
    
    FOLDER_MAP = {
        'inbox': FOLDER_INBOX,
        'sent': FOLDER_SENT,
        'sent items': FOLDER_SENT,
        'outbox': FOLDER_OUTBOX,
        'drafts': FOLDER_DRAFTS,
        'deleted': FOLDER_DELETED,
        'deleted items': FOLDER_DELETED,
        'junk': FOLDER_JUNK,
        'junk email': FOLDER_JUNK,
    }
    
    def __init__(self):
        self.outlook = None
        self.namespace = None
        self._html_converter = html2text.HTML2Text()
        self._html_converter.ignore_links = False
        self._html_converter.ignore_images = True
        self._html_converter.body_width = 0
        
    def connect(self) -> bool:
        """Connect to Outlook application."""
        if not OUTLOOK_AVAILABLE:
            logger.error("Outlook connection not available on this platform")
            return False
            
        try:
            pythoncom.CoInitialize()
            self.outlook = win32com.client.Dispatch("Outlook.Application")
            self.namespace = self.outlook.GetNamespace("MAPI")
            logger.info("Connected to Outlook")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Outlook: {e}")
            return False
            
    def disconnect(self):
        """Disconnect from Outlook."""
        self.outlook = None
        self.namespace = None
        try:
            pythoncom.CoUninitialize()
        except:
            pass
            
    def __enter__(self):
        self.connect()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        
    def _get_folder(self, folder_name: str):
        """Get a folder by name or type."""
        if not self.namespace:
            return None
            
        # Check if it's a known folder type
        folder_lower = folder_name.lower()
        if folder_lower in self.FOLDER_MAP:
            try:
                return self.namespace.GetDefaultFolder(self.FOLDER_MAP[folder_lower])
            except:
                pass
                
        # Try to find by name in all stores
        try:
            for store in self.namespace.Stores:
                try:
                    root = store.GetRootFolder()
                    folder = self._find_folder_recursive(root, folder_name)
                    if folder:
                        return folder
                except:
                    continue
        except:
            pass
            
        return None
        
    def _find_folder_recursive(self, parent, target_name: str):
        """Recursively search for a folder by name."""
        try:
            for folder in parent.Folders:
                if folder.Name.lower() == target_name.lower():
                    return folder
                # Recurse
                result = self._find_folder_recursive(folder, target_name)
                if result:
                    return result
        except:
            pass
        return None
        
    def _extract_body_text(self, mail_item) -> tuple[str, str]:
        """Extract plain text and HTML body."""
        body_text = ""
        body_html = ""
        
        try:
            body_text = mail_item.Body or ""
        except:
            pass
            
        try:
            body_html = mail_item.HTMLBody or ""
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
        
    def _extract_recipients(self, mail_item) -> tuple[List[str], List[str], List[str]]:
        """Extract recipients."""
        to_list = []
        cc_list = []
        bcc_list = []
        
        try:
            if mail_item.To:
                to_list = [r.strip() for r in mail_item.To.split(';') if r.strip()]
        except:
            pass
            
        try:
            if mail_item.CC:
                cc_list = [r.strip() for r in mail_item.CC.split(';') if r.strip()]
        except:
            pass
            
        try:
            if mail_item.BCC:
                bcc_list = [r.strip() for r in mail_item.BCC.split(';') if r.strip()]
        except:
            pass
            
        return to_list, cc_list, bcc_list
        
    def _parse_mail_item(self, mail_item, folder_name: str) -> Optional[EmailMessage]:
        """Parse an Outlook mail item into an EmailMessage."""
        try:
            # Skip non-mail items
            if mail_item.Class != 43:  # olMail = 43
                return None
                
            # Message ID
            message_id = ""
            try:
                # Property tag for PR_INTERNET_MESSAGE_ID
                message_id = mail_item.PropertyAccessor.GetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
                )
            except:
                message_id = mail_item.EntryID or ""
                
            # Conversation ID
            conversation_id = ""
            try:
                conversation_id = mail_item.ConversationID or mail_item.ConversationTopic or ""
            except:
                pass
                
            # Sender
            sender = ""
            sender_name = ""
            try:
                sender = mail_item.SenderEmailAddress or ""
                sender_name = mail_item.SenderName or ""
            except:
                pass
                
            # Subject
            subject = ""
            try:
                subject = mail_item.Subject or ""
            except:
                pass
                
            # Dates
            sent_date = None
            received_date = None
            try:
                if mail_item.SentOn:
                    sent_date = datetime.fromtimestamp(mail_item.SentOn.timestamp())
            except:
                pass
            try:
                if mail_item.ReceivedTime:
                    received_date = datetime.fromtimestamp(mail_item.ReceivedTime.timestamp())
            except:
                pass
                
            # Recipients
            to_list, cc_list, bcc_list = self._extract_recipients(mail_item)
            
            # Body
            body_text, body_html = self._extract_body_text(mail_item)
            
            # Direction
            folder_lower = folder_name.lower()
            if 'sent' in folder_lower or 'outbox' in folder_lower:
                direction = EmailDirection.SENT
            else:
                direction = EmailDirection.RECEIVED
                
            # Status
            is_read = False
            is_replied = False
            is_forwarded = False
            is_flagged = False
            
            try:
                is_read = not mail_item.UnRead
            except:
                pass
                
            try:
                # Check reply/forward flags
                # 0x0004 = replied, 0x0008 = forwarded
                last_verb = mail_item.PropertyAccessor.GetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x10810003"
                )
                is_replied = (last_verb == 102 or last_verb == 103)  # Reply or Reply All
                is_forwarded = (last_verb == 104)  # Forward
            except:
                pass
                
            try:
                is_flagged = mail_item.FlagStatus == 2  # olFlagMarked
            except:
                pass
                
            # Importance
            importance = "normal"
            try:
                imp = mail_item.Importance
                if imp == 2:
                    importance = "high"
                elif imp == 0:
                    importance = "low"
            except:
                pass
                
            # Attachments
            has_attachments = False
            try:
                has_attachments = mail_item.Attachments.Count > 0
            except:
                pass
                
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
                source='outlook',
                direction=direction,
                is_read=is_read,
                is_replied=is_replied,
                is_forwarded=is_forwarded,
                is_flagged=is_flagged,
                importance=importance,
                has_attachments=has_attachments,
            )
            
        except Exception as e:
            logger.debug(f"Failed to parse mail item: {e}")
            return None
            
    def get_emails(self,
                   folders: Optional[List[str]] = None,
                   since: Optional[datetime] = None,
                   limit: Optional[int] = None) -> Generator[EmailMessage, None, None]:
        """
        Get emails from Outlook.
        
        Args:
            folders: List of folder names to search (default: Inbox, Sent Items)
            since: Only get emails after this date
            limit: Maximum number of emails to return per folder
            
        Yields:
            EmailMessage objects
        """
        if not self.namespace:
            logger.error("Not connected to Outlook")
            return
            
        if folders is None:
            folders = ['Inbox', 'Sent Items']
            
        for folder_name in folders:
            folder = self._get_folder(folder_name)
            if not folder:
                logger.warning(f"Could not find folder: {folder_name}")
                continue
                
            logger.info(f"Processing folder: {folder_name}")
            
            try:
                items = folder.Items
                items.Sort("[ReceivedTime]", True)  # Sort descending
                
                count = 0
                for item in items:
                    # Check limit
                    if limit and count >= limit:
                        break
                        
                    email = self._parse_mail_item(item, folder_name)
                    if email:
                        # Check date filter
                        if since and email.date and email.date < since:
                            # Since we're sorted descending, we can stop
                            break
                            
                        yield email
                        count += 1
                        
            except Exception as e:
                logger.error(f"Error processing folder {folder_name}: {e}")
                continue
                
    def get_user_email(self) -> Optional[str]:
        """Get the current user's email address."""
        if not self.namespace:
            return None
        try:
            return self.namespace.CurrentUser.Address
        except:
            return None
            
    def get_folder_list(self) -> List[str]:
        """Get list of all mail folders."""
        folders = []
        
        if not self.namespace:
            return folders
            
        def collect_folders(parent, path=""):
            try:
                for folder in parent.Folders:
                    current = f"{path}/{folder.Name}" if path else folder.Name
                    folders.append(current)
                    collect_folders(folder, current)
            except:
                pass
                
        try:
            for store in self.namespace.Stores:
                try:
                    root = store.GetRootFolder()
                    collect_folders(root, store.DisplayName)
                except:
                    continue
        except:
            pass
            
        return folders


def get_outlook_emails(folders: Optional[List[str]] = None,
                       since: Optional[datetime] = None,
                       limit: Optional[int] = None) -> Generator[EmailMessage, None, None]:
    """
    Convenience function to get emails from Outlook.
    
    Args:
        folders: Folder names to search
        since: Date filter
        limit: Max emails per folder
        
    Yields:
        EmailMessage objects
    """
    if not OUTLOOK_AVAILABLE:
        logger.error("Outlook connection not available")
        return
        
    with OutlookConnection() as conn:
        yield from conn.get_emails(folders=folders, since=since, limit=limit)
