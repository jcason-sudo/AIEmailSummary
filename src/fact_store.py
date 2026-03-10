"""
SQLite storage for fact cards.

Stores structured email knowledge in a queryable relational format,
separate from ChromaDB's vector store.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from fact_cards import FactCard
import config

logger = logging.getLogger(__name__)

DB_FILE = config.DB_PATH / "fact_cards.db"


class FactStore:
    """SQLite-backed storage for extracted fact cards."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or DB_FILE)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fact_cards (
                    email_id TEXT PRIMARY KEY,
                    raw_json TEXT NOT NULL,
                    sentiment TEXT DEFAULT 'neutral',
                    extracted_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    FOREIGN KEY (email_id) REFERENCES fact_cards(email_id)
                );
                CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(entity_name);
                CREATE INDEX IF NOT EXISTS idx_entities_email ON entities(email_id);

                CREATE TABLE IF NOT EXISTS intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    FOREIGN KEY (email_id) REFERENCES fact_cards(email_id)
                );
                CREATE INDEX IF NOT EXISTS idx_intents_intent ON intents(intent);

                CREATE TABLE IF NOT EXISTS commitments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL,
                    who TEXT,
                    what TEXT,
                    by_when TEXT,
                    FOREIGN KEY (email_id) REFERENCES fact_cards(email_id)
                );
                CREATE INDEX IF NOT EXISTS idx_commitments_who ON commitments(who);

                CREATE TABLE IF NOT EXISTS action_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL,
                    description TEXT,
                    assignee TEXT,
                    deadline TEXT,
                    FOREIGN KEY (email_id) REFERENCES fact_cards(email_id)
                );
                CREATE INDEX IF NOT EXISTS idx_actions_assignee ON action_items(assignee);
            """)
        logger.info(f"Fact store initialized at {self.db_path}")

    def save_card(self, card: FactCard):
        """Save a fact card and its related entities."""
        with sqlite3.connect(self.db_path) as conn:
            # Upsert main card
            conn.execute(
                "INSERT OR REPLACE INTO fact_cards (email_id, raw_json, sentiment, extracted_at) VALUES (?, ?, ?, ?)",
                (card.email_id, json.dumps(card.to_dict()), card.sentiment, datetime.now().isoformat())
            )

            # Clear old related data for this email
            for table in ('entities', 'intents', 'commitments', 'action_items'):
                conn.execute(f"DELETE FROM {table} WHERE email_id = ?", (card.email_id,))

            # Insert entities
            for entity in card.entities:
                conn.execute(
                    "INSERT INTO entities (email_id, entity_name) VALUES (?, ?)",
                    (card.email_id, entity)
                )

            # Insert intents
            for intent in card.intents:
                conn.execute(
                    "INSERT INTO intents (email_id, intent) VALUES (?, ?)",
                    (card.email_id, intent)
                )

            # Insert commitments
            for c in card.commitments:
                conn.execute(
                    "INSERT INTO commitments (email_id, who, what, by_when) VALUES (?, ?, ?, ?)",
                    (card.email_id, c.get('who', ''), c.get('what', ''), c.get('by_when', ''))
                )

            # Insert action items
            for a in card.action_items:
                conn.execute(
                    "INSERT INTO action_items (email_id, description, assignee, deadline) VALUES (?, ?, ?, ?)",
                    (card.email_id, a.get('description', ''), a.get('assignee', ''), a.get('deadline', ''))
                )

    def save_cards(self, cards: List[FactCard]):
        """Save multiple fact cards."""
        for card in cards:
            self.save_card(card)

    def get_card(self, email_id: str) -> Optional[FactCard]:
        """Get a fact card by email ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT raw_json FROM fact_cards WHERE email_id = ?", (email_id,)
            ).fetchone()
            if row:
                return FactCard.from_dict(json.loads(row[0]))
        return None

    def get_extracted_count(self) -> int:
        """Get total number of extracted fact cards."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM fact_cards").fetchone()
            return row[0] if row else 0

    def get_unextracted_ids(self, all_ids: List[str]) -> List[str]:
        """Find email IDs that don't have fact cards yet."""
        if not all_ids:
            return []

        with sqlite3.connect(self.db_path) as conn:
            placeholders = ','.join('?' * len(all_ids))
            rows = conn.execute(
                f"SELECT email_id FROM fact_cards WHERE email_id IN ({placeholders})",
                all_ids
            ).fetchall()
            extracted = set(r[0] for r in rows)

        return [eid for eid in all_ids if eid not in extracted]

    def search_entities(self, entity_name: str) -> List[FactCard]:
        """Find fact cards mentioning a specific entity."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT DISTINCT fc.raw_json FROM fact_cards fc
                   JOIN entities e ON fc.email_id = e.email_id
                   WHERE e.entity_name LIKE ?""",
                (f"%{entity_name}%",)
            ).fetchall()
            return [FactCard.from_dict(json.loads(r[0])) for r in rows]

    def search_intents(self, intent: str) -> List[FactCard]:
        """Find fact cards with a specific intent."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT DISTINCT fc.raw_json FROM fact_cards fc
                   JOIN intents i ON fc.email_id = i.email_id
                   WHERE i.intent = ?""",
                (intent,)
            ).fetchall()
            return [FactCard.from_dict(json.loads(r[0])) for r in rows]

    def get_commitments(self, person: Optional[str] = None) -> List[dict]:
        """Get all commitments, optionally filtered by person."""
        with sqlite3.connect(self.db_path) as conn:
            if person:
                rows = conn.execute(
                    "SELECT email_id, who, what, by_when FROM commitments WHERE who LIKE ?",
                    (f"%{person}%",)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT email_id, who, what, by_when FROM commitments"
                ).fetchall()

        return [
            {'email_id': r[0], 'who': r[1], 'what': r[2], 'by_when': r[3]}
            for r in rows
        ]

    def get_action_items(self, assignee: Optional[str] = None) -> List[dict]:
        """Get all action items, optionally filtered by assignee."""
        with sqlite3.connect(self.db_path) as conn:
            if assignee:
                rows = conn.execute(
                    "SELECT email_id, description, assignee, deadline FROM action_items WHERE assignee LIKE ?",
                    (f"%{assignee}%",)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT email_id, description, assignee, deadline FROM action_items"
                ).fetchall()

        return [
            {'email_id': r[0], 'description': r[1], 'assignee': r[2], 'deadline': r[3]}
            for r in rows
        ]

    def get_stats(self) -> dict:
        """Get fact store statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cards = conn.execute("SELECT COUNT(*) FROM fact_cards").fetchone()[0]
            entities = conn.execute("SELECT COUNT(DISTINCT entity_name) FROM entities").fetchone()[0]
            commitments = conn.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
            actions = conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]

        return {
            'total_cards': cards,
            'unique_entities': entities,
            'total_commitments': commitments,
            'total_action_items': actions,
        }


# Module-level singleton
_store: Optional[FactStore] = None


def get_fact_store() -> FactStore:
    global _store
    if _store is None:
        _store = FactStore()
    return _store
