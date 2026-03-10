"""
Sync state management: watermarks, deduplication, and priority queues.

Tracks per-account sync progress so incremental sync only fetches new messages.
Provides body-hash deduplication to catch near-duplicates across folders.
Supports change detection for email status updates (read/replied/flagged).
"""

import json
import hashlib
import logging
import sqlite3
import re
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import config

logger = logging.getLogger(__name__)

DB_FILE = config.DB_PATH / "sync_state.db"


class EmailCheckResult(Enum):
    """Result of checking an email against sync state."""
    NEW = "new"
    DUPLICATE = "duplicate"
    CHANGED = "changed"


def _normalize_body(text: str) -> str:
    """Normalize email body for dedup hashing.

    Strips whitespace variations, signatures, and common noise
    so the same email forwarded or stored in multiple folders
    produces the same hash.
    """
    if not text:
        return ""
    # Lowercase
    text = text.lower()
    # Strip all whitespace to a single space
    text = re.sub(r'\s+', ' ', text)
    # Remove common noise
    text = re.sub(r'sent from my \w+', '', text)
    text = re.sub(r'get outlook for \w+', '', text)
    # Take first 2000 chars (enough to identify, avoids hash on huge bodies)
    return text[:2000].strip()


def body_hash(text: str) -> str:
    """Generate a normalized body hash for deduplication."""
    normalized = _normalize_body(text)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class SyncState:
    """SQLite-backed sync state tracking."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or DB_FILE)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sync_watermarks (
                    account_id TEXT PRIMARY KEY,
                    account_label TEXT,
                    last_sync_at TEXT NOT NULL,
                    last_message_date TEXT,
                    messages_synced INTEGER DEFAULT 0,
                    folders_synced TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'idle'
                );

                CREATE TABLE IF NOT EXISTS seen_messages (
                    message_id_hash TEXT PRIMARY KEY,
                    body_hash TEXT,
                    email_id TEXT,
                    first_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_seen_body ON seen_messages(body_hash);

                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    messages_found INTEGER DEFAULT 0,
                    messages_new INTEGER DEFAULT 0,
                    messages_skipped_dup INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    error TEXT
                );
            """)
            # Migration: add status columns to seen_messages if missing
            cursor = conn.execute("PRAGMA table_info(seen_messages)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'is_read' not in columns:
                conn.execute("ALTER TABLE seen_messages ADD COLUMN is_read INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE seen_messages ADD COLUMN is_replied INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE seen_messages ADD COLUMN is_flagged INTEGER DEFAULT 0")
                logger.info("Migrated seen_messages: added status columns")

    # --- Watermark management ---

    def get_watermark(self, account_id: str) -> Optional[dict]:
        """Get the sync watermark for an account."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT account_label, last_sync_at, last_message_date, messages_synced, folders_synced, status "
                "FROM sync_watermarks WHERE account_id = ?",
                (account_id,)
            ).fetchone()
            if row:
                return {
                    'account_id': account_id,
                    'account_label': row[0],
                    'last_sync_at': row[1],
                    'last_message_date': row[2],
                    'messages_synced': row[3],
                    'folders_synced': json.loads(row[4]) if row[4] else [],
                    'status': row[5],
                }
        return None

    def get_all_watermarks(self) -> List[dict]:
        """Get watermarks for all accounts."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT account_id, account_label, last_sync_at, last_message_date, messages_synced, folders_synced, status "
                "FROM sync_watermarks ORDER BY last_sync_at DESC"
            ).fetchall()
            return [{
                'account_id': r[0],
                'account_label': r[1],
                'last_sync_at': r[2],
                'last_message_date': r[3],
                'messages_synced': r[4],
                'folders_synced': json.loads(r[5]) if r[5] else [],
                'status': r[6],
            } for r in rows]

    def update_watermark(self, account_id: str, label: str,
                         last_message_date: Optional[str] = None,
                         messages_synced: int = 0,
                         folders: Optional[List[str]] = None,
                         status: str = 'idle'):
        """Update or create a sync watermark."""
        now = datetime.now().isoformat()
        folders_json = json.dumps(folders or [])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO sync_watermarks (account_id, account_label, last_sync_at, last_message_date, messages_synced, folders_synced, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_sync_at = ?,
                    last_message_date = COALESCE(?, last_message_date),
                    messages_synced = messages_synced + ?,
                    folders_synced = ?,
                    status = ?
            """, (account_id, label, now, last_message_date, messages_synced, folders_json, status,
                  now, last_message_date, messages_synced, folders_json, status))

    # --- Deduplication & Change Detection ---

    def is_duplicate(self, message_id: str, body_text: str = "") -> bool:
        """Check if a message is a duplicate by Message-ID or body hash.

        Legacy method — use check_email() for change detection support.
        """
        result = self.check_email(message_id, body_text)
        return result != EmailCheckResult.NEW

    def check_email(self, message_id: str, body_text: str = "",
                    is_read: bool = False, is_replied: bool = False,
                    is_flagged: bool = False) -> EmailCheckResult:
        """Check if a message is new, duplicate, or has changed status.

        Returns EmailCheckResult.NEW, DUPLICATE, or CHANGED.
        """
        mid_hash = hashlib.sha256(message_id.encode()).hexdigest()[:32] if message_id else ""

        with sqlite3.connect(self.db_path) as conn:
            # Check by Message-ID hash
            if mid_hash:
                row = conn.execute(
                    "SELECT is_read, is_replied, is_flagged FROM seen_messages WHERE message_id_hash = ?",
                    (mid_hash,)
                ).fetchone()
                if row:
                    old_read, old_replied, old_flagged = row
                    if (bool(old_read) != is_read or
                            bool(old_replied) != is_replied or
                            bool(old_flagged) != is_flagged):
                        return EmailCheckResult.CHANGED
                    return EmailCheckResult.DUPLICATE

            # Check by body hash (catches same email in multiple folders)
            if body_text:
                bhash = body_hash(body_text)
                row = conn.execute(
                    "SELECT 1 FROM seen_messages WHERE body_hash = ?", (bhash,)
                ).fetchone()
                if row:
                    return EmailCheckResult.DUPLICATE

        return EmailCheckResult.NEW

    def mark_seen(self, message_id: str, email_id: str, body_text: str = "",
                  is_read: bool = False, is_replied: bool = False,
                  is_flagged: bool = False):
        """Mark a message as seen for dedup tracking, including status."""
        mid_hash = hashlib.sha256(message_id.encode()).hexdigest()[:32] if message_id else email_id
        bhash = body_hash(body_text) if body_text else ""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_messages "
                "(message_id_hash, body_hash, email_id, first_seen_at, is_read, is_replied, is_flagged) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mid_hash, bhash, email_id, now,
                 int(is_read), int(is_replied), int(is_flagged))
            )

    def update_seen_status(self, message_id: str,
                           is_read: bool, is_replied: bool, is_flagged: bool):
        """Update status columns for an already-seen message."""
        mid_hash = hashlib.sha256(message_id.encode()).hexdigest()[:32] if message_id else ""
        if not mid_hash:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE seen_messages SET is_read=?, is_replied=?, is_flagged=? "
                "WHERE message_id_hash=?",
                (int(is_read), int(is_replied), int(is_flagged), mid_hash)
            )

    def get_seen_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()
            return row[0] if row else 0

    # --- Incremental sync ---

    def get_incremental_since(self, account_id: str, fallback_since: datetime) -> datetime:
        """Get the effective 'since' date for incremental sync.

        If the account has a watermark with last_message_date, use that
        (minus 1 hour overlap buffer). Otherwise use fallback_since.
        """
        wm = self.get_watermark(account_id)
        if wm and wm.get('last_message_date'):
            try:
                last = datetime.fromisoformat(wm['last_message_date'])
                # 1-hour overlap buffer for safety
                incremental_since = last - timedelta(hours=1)
                # Use the more recent of incremental vs fallback
                if incremental_since > fallback_since:
                    logger.info(
                        f"Incremental sync for {account_id}: "
                        f"resuming from {incremental_since.isoformat()} "
                        f"(watermark: {wm['last_message_date']})"
                    )
                    return incremental_since
            except (ValueError, TypeError):
                pass
        return fallback_since

    # --- Retention cleanup ---

    def cleanup_old_seen(self, retention_days: int) -> int:
        """Remove seen_messages entries older than retention window."""
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM seen_messages WHERE first_seen_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
        if deleted:
            logger.info(f"Cleaned up {deleted} old seen_messages entries (>{retention_days} days)")
        return deleted

    # --- Sync log ---

    def start_sync_log(self, account_id: str) -> int:
        """Start a sync log entry. Returns the log ID."""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sync_log (account_id, started_at, status) VALUES (?, ?, 'running')",
                (account_id, now)
            )
            return cursor.lastrowid

    def complete_sync_log(self, log_id: int, messages_found: int, messages_new: int,
                          messages_skipped: int, status: str = 'completed', error: str = ''):
        """Complete a sync log entry."""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sync_log SET completed_at=?, messages_found=?, messages_new=?, "
                "messages_skipped_dup=?, status=?, error=? WHERE id=?",
                (now, messages_found, messages_new, messages_skipped, status, error, log_id)
            )

    def get_sync_history(self, account_id: Optional[str] = None, limit: int = 10) -> List[dict]:
        """Get recent sync history."""
        with sqlite3.connect(self.db_path) as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT account_id, started_at, completed_at, messages_found, messages_new, "
                    "messages_skipped_dup, status, error FROM sync_log "
                    "WHERE account_id=? ORDER BY id DESC LIMIT ?",
                    (account_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT account_id, started_at, completed_at, messages_found, messages_new, "
                    "messages_skipped_dup, status, error FROM sync_log "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [{
                'account_id': r[0], 'started_at': r[1], 'completed_at': r[2],
                'messages_found': r[3], 'messages_new': r[4],
                'messages_skipped_dup': r[5], 'status': r[6], 'error': r[7],
            } for r in rows]

    def get_stats(self) -> dict:
        """Get overall sync statistics."""
        with sqlite3.connect(self.db_path) as conn:
            watermarks = conn.execute("SELECT COUNT(*) FROM sync_watermarks").fetchone()[0]
            seen = conn.execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0]
            syncs = conn.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
            total_new = conn.execute(
                "SELECT COALESCE(SUM(messages_new), 0) FROM sync_log WHERE status='completed'"
            ).fetchone()[0]
            total_dup = conn.execute(
                "SELECT COALESCE(SUM(messages_skipped_dup), 0) FROM sync_log WHERE status='completed'"
            ).fetchone()[0]
        return {
            'accounts_tracked': watermarks,
            'messages_seen': seen,
            'total_syncs': syncs,
            'total_new_messages': total_new,
            'total_duplicates_skipped': total_dup,
        }


# Singleton
_state: Optional[SyncState] = None

def get_sync_state() -> SyncState:
    global _state
    if _state is None:
        _state = SyncState()
    return _state
