"""
Configuration - minimal, no abstractions.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# Database
DB_PATH = Path(os.getenv("DB_PATH", Path.home() / ".inboxai" / "chroma"))
DB_PATH.mkdir(parents=True, exist_ok=True)

# Embedding model (local, runs on CPU)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Email ingestion
EMAIL_LOOKBACK_DAYS = int(os.getenv("EMAIL_LOOKBACK_DAYS", "365"))
OUTLOOK_FOLDERS = [f.strip() for f in os.getenv("OUTLOOK_FOLDERS", "Inbox,Sent Items").split(",")]

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))
