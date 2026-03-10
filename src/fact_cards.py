"""
Fact Card data model for structured email knowledge.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FactCard:
    """Structured knowledge extracted from a single email."""

    email_id: str
    entities: List[str] = field(default_factory=list)
    intents: List[str] = field(default_factory=list)
    commitments: List[dict] = field(default_factory=list)
    action_items: List[dict] = field(default_factory=list)
    key_facts: List[str] = field(default_factory=list)
    sentiment: str = "neutral"
    topics: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'email_id': self.email_id,
            'entities': self.entities,
            'intents': self.intents,
            'commitments': self.commitments,
            'action_items': self.action_items,
            'key_facts': self.key_facts,
            'sentiment': self.sentiment,
            'topics': self.topics,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FactCard':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
