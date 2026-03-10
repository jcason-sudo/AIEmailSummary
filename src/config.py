"""
Configuration - minimal, no abstractions.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# llama.cpp server (Vulkan GPU)
LLAMACPP_URL = os.getenv("LLAMACPP_URL", "http://localhost:8080")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama-3.2-3b-instruct")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# Claude API (optional, for cloud-based analysis)
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Database
DB_PATH = Path(os.getenv("DB_PATH", Path.home() / ".inboxai" / "chroma"))
DB_PATH.mkdir(parents=True, exist_ok=True)

# Embedding model (local, runs on CPU)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Email ingestion
EMAIL_LOOKBACK_DAYS = int(os.getenv("EMAIL_LOOKBACK_DAYS", "365"))
EMAIL_RETENTION_DAYS = int(os.getenv("EMAIL_RETENTION_DAYS", "365"))
OUTLOOK_FOLDERS = [f.strip() for f in os.getenv("OUTLOOK_FOLDERS", "Inbox,Sent Items,Zoom AI summary,Confluence,Drafts").split(",")]

# IMAP accounts (Gmail, Yahoo, etc.)
# Format: "provider:user@email.com:app_password;provider:user@email.com:app_password"
# Providers: gmail, yahoo, outlook, hotmail
IMAP_ACCOUNTS = os.getenv("IMAP_ACCOUNTS", "")

# User identity — used by state engine to determine "sent by me"
# Auto-populated from IMAP accounts + manual override
_manual_addresses = [e.strip().lower() for e in os.getenv("MY_EMAIL_ADDRESSES", "").split(",") if e.strip()]
_imap_addresses = []
if IMAP_ACCOUNTS:
    for entry in IMAP_ACCOUNTS.split(";"):
        parts = entry.strip().split(":")
        if len(parts) >= 2:
            _imap_addresses.append(parts[1].strip().lower())
MY_EMAIL_ADDRESSES = list(set(_manual_addresses + _imap_addresses))

# Automated/bot senders — emails from these are tagged as "meeting_note"
# Content is attributed to the user, not treated as an external person
AUTOMATED_SENDERS = {s.strip().lower() for s in os.getenv(
    "AUTOMATED_SENDERS",
    "no-reply@zoom.us,noreply@zoom.us,no-reply@zoom.com,noreply@zoom.com"
).split(",") if s.strip()}

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))
