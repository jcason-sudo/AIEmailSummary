"""
Outlook Calendar connection module.
Connects to the running Outlook application via COM (Windows only).
"""

import sys
import logging
from datetime import datetime, timedelta
from typing import Generator, Optional, List

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    try:
        import win32com.client
        import pythoncom
        CALENDAR_AVAILABLE = True
    except ImportError:
        CALENDAR_AVAILABLE = False
        logger.warning("pywin32 not installed. Calendar connection unavailable.")
else:
    CALENDAR_AVAILABLE = False
    logger.info("Not on Windows. Outlook Calendar connection unavailable.")


class CalendarConnection:
    """
    Connection to Outlook Calendar via COM interface.
    Windows only - requires pywin32.
    """

    FOLDER_CALENDAR = 9

    BUSY_STATUS_MAP = {
        0: 'free',
        1: 'tentative',
        2: 'busy',
        3: 'out_of_office',
    }

    def __init__(self):
        self.outlook = None
        self.namespace = None

    def connect(self) -> bool:
        """Connect to Outlook application."""
        if not CALENDAR_AVAILABLE:
            logger.error("Calendar connection not available on this platform")
            return False

        try:
            pythoncom.CoInitialize()
            self.outlook = win32com.client.Dispatch("Outlook.Application")
            self.namespace = self.outlook.GetNamespace("MAPI")
            logger.info("Connected to Outlook Calendar")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Outlook Calendar: {e}")
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

    def _parse_attendees(self, attendee_string: str) -> List[str]:
        """Parse semicolon-separated attendee string into list."""
        if not attendee_string:
            return []
        return [a.strip() for a in attendee_string.split(';') if a.strip()]

    def get_meetings(self, start: datetime, end: datetime) -> List[dict]:
        """Get calendar meetings in a date range.

        Returns list of meeting dicts (not dataclass, to avoid import issues).
        """
        if not self.namespace:
            logger.error("Not connected to Outlook")
            return []

        try:
            calendar = self.namespace.GetDefaultFolder(self.FOLDER_CALENDAR)
        except Exception as e:
            logger.error(f"Could not access calendar folder: {e}")
            return []

        meetings = []

        try:
            items = calendar.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            # Restrict to date range
            start_str = start.strftime("%m/%d/%Y %H:%M %p")
            end_str = end.strftime("%m/%d/%Y %H:%M %p")
            restriction = (
                f"[Start] >= '{start_str}' AND [Start] < '{end_str}'"
            )
            filtered = items.Restrict(restriction)

            for item in filtered:
                try:
                    # Skip non-appointment items
                    if item.Class != 26:
                        continue

                    subject = ""
                    try:
                        subject = item.Subject or ""
                    except:
                        pass

                    item_start = None
                    item_end = None
                    try:
                        item_start = datetime.fromtimestamp(item.Start.timestamp())
                    except:
                        pass
                    try:
                        item_end = datetime.fromtimestamp(item.End.timestamp())
                    except:
                        pass

                    location = ""
                    try:
                        location = item.Location or ""
                    except:
                        pass

                    body = ""
                    try:
                        body = item.Body or ""
                    except:
                        pass

                    organizer = ""
                    try:
                        organizer = item.Organizer or ""
                    except:
                        pass

                    required = []
                    try:
                        req_str = item.RequiredAttendees or ""
                        required = self._parse_attendees(req_str)
                    except:
                        pass

                    optional = []
                    try:
                        opt_str = item.OptionalAttendees or ""
                        optional = self._parse_attendees(opt_str)
                    except:
                        pass

                    is_all_day = False
                    try:
                        is_all_day = bool(item.AllDayEvent)
                    except:
                        pass

                    is_recurring = False
                    try:
                        is_recurring = bool(item.IsRecurring)
                    except:
                        pass

                    busy_status = "busy"
                    try:
                        busy_status = self.BUSY_STATUS_MAP.get(item.BusyStatus, "busy")
                    except:
                        pass

                    meetings.append({
                        'subject': subject,
                        'start': item_start.isoformat() if item_start else '',
                        'end': item_end.isoformat() if item_end else '',
                        'duration_minutes': int((item_end - item_start).total_seconds() / 60) if item_start and item_end else 0,
                        'location': location,
                        'body': body[:2000],
                        'organizer': organizer,
                        'required_attendees': required,
                        'optional_attendees': optional,
                        'all_attendees': required + optional,
                        'is_all_day': is_all_day,
                        'is_recurring': is_recurring,
                        'busy_status': busy_status,
                    })

                except Exception as e:
                    logger.debug(f"Failed to parse calendar item: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error reading calendar: {e}")

        logger.info(f"Found {len(meetings)} meetings between {start} and {end}")
        return meetings

    def get_next_business_day_meetings(self) -> dict:
        """Get meetings for the next business day.

        Skips weekends: Fri→Mon, Sat→Mon, Sun→Mon.
        """
        today = datetime.now()
        weekday = today.weekday()  # 0=Mon ... 6=Sun

        if weekday == 4:  # Friday
            days_ahead = 3
        elif weekday == 5:  # Saturday
            days_ahead = 2
        elif weekday == 6:  # Sunday
            days_ahead = 1
        else:
            days_ahead = 1

        target = today + timedelta(days=days_ahead)
        start = target.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        meetings = self.get_meetings(start, end)

        return {
            'date': start.strftime('%A, %B %d, %Y'),
            'date_iso': start.isoformat(),
            'meeting_count': len(meetings),
            'meetings': meetings,
        }

    def get_upcoming_meetings(self, days: int = 7) -> dict:
        """Get meetings for the next N days including recurring meetings.

        IncludeRecurrences is already set in get_meetings(), so recurring
        meetings created long ago will appear as expanded occurrences within
        the date range.
        """
        today = datetime.now()
        start = today.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        end = start + timedelta(days=days)

        meetings = self.get_meetings(start, end)

        # Group by date for easier frontend display
        by_date = {}
        for m in meetings:
            if m['start']:
                date_key = m['start'][:10]  # YYYY-MM-DD
            else:
                date_key = 'unknown'
            by_date.setdefault(date_key, []).append(m)

        return {
            'start_date': start.strftime('%A, %B %d, %Y'),
            'end_date': (end - timedelta(days=1)).strftime('%A, %B %d, %Y'),
            'start_iso': start.isoformat(),
            'end_iso': end.isoformat(),
            'days': days,
            'meeting_count': len(meetings),
            'meetings': meetings,
            'by_date': by_date,
        }


def get_calendar_meetings(days: int = 7) -> dict:
    """Convenience function to get upcoming meetings."""
    if not CALENDAR_AVAILABLE:
        return {
            'start_date': '',
            'end_date': '',
            'meeting_count': 0,
            'meetings': [],
            'by_date': {},
            'error': 'Calendar not available (Windows + Outlook required)'
        }

    with CalendarConnection() as conn:
        return conn.get_upcoming_meetings(days=days)
