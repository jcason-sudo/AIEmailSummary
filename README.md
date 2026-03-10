# InboxAI — Local Email Intelligence

A privacy-first email RAG (Retrieval-Augmented Generation) application that uses local LLMs to answer questions about your inbox. All data stays on your machine — no cloud services required.

InboxAI ingests emails from multiple sources, builds a searchable knowledge base using hybrid retrieval (semantic + keyword), and lets you chat with your inbox using natural language.

---

## Features

### Email Intelligence
- **Natural Language Chat** — Ask questions about your inbox in plain English with streaming AI responses
- **Hybrid Search** — BM25 keyword + semantic vector search with Reciprocal Rank Fusion and cross-encoder reranking
- **Thread Awareness** — Groups conversations, tracks reply chains, and generates thread summaries
- **Action Item Detection** — Automatically identifies emails needing your response vs. awaiting replies

### Email Sources
- **Outlook Desktop** — Live connection via COM interface (Windows)
- **IMAP Accounts** — Gmail, Yahoo, Outlook.com, or any standard IMAP server
- **PST/OST Files** — Import archived mailbox files directly
- **Attachment Extraction** — Indexes content from PDF, DOCX, XLSX, PPTX, TXT, CSV, HTML, and more

### Analysis & Research
- **Deep Research** — Comprehensive topic analysis with timeline, thread arcs, and cited synthesis
- **Entity Map** — Interactive graph visualization of people, topics, and organizations with relationship edges
- **Meeting Prep** — AI-generated briefing docs from Outlook Calendar meetings with related email context
- **Fact Cards** — Structured extraction of entities, commitments, action items, and sentiment from emails
- **Analytics Dashboard** — Email volume charts, sender frequency, folder breakdown

### Architecture
- **3-Tier Retrieval** — Semantic search → BM25 fusion → cross-encoder reranking
- **Incremental Sync** — Watermark-based sync with deduplication (Message-ID + body hash)
- **State Engine** — Deterministic thread classification (needs_action, awaiting_response, completed, stale)
- **Dual LLM Backend** — Switch between local models (Ollama/llama.cpp) and Claude API

---

## System Requirements

- **OS**: Windows 10/11
- **Python**: 3.10+
- **RAM**: 8GB minimum, 16GB+ recommended
- **GPU**: Optional but recommended for local LLM inference (AMD or NVIDIA)
- **Outlook Desktop**: Required for Outlook connector and calendar features (optional if using IMAP/PST only)

---

## Quick Start

### 1. Install a Local LLM

**Option A: Ollama** (simplest)
```powershell
# Download from https://ollama.com/download, then:
ollama pull llama3.2:3b
```

**Option B: llama.cpp with Vulkan** (better AMD GPU support)
```powershell
# See llama.cpp docs for Vulkan build instructions
# Start server on port 8080
```

### 2. Set Up the Project

```powershell
git clone https://github.com/jcason-sudo/AIEmailSummary.git
cd AIEmailSummary

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure Environment

Copy the example and edit:
```powershell
copy .env.example .env
```

Key settings in `.env`:
```env
# LLM Backend
LLAMACPP_URL=http://localhost:8080
OLLAMA_MODEL=llama-3.1-8b-instruct
LLM_TEMPERATURE=0.3

# Email
EMAIL_LOOKBACK_DAYS=365

# Optional: Claude API for cloud-powered analysis
CLAUDE_API_KEY=your-key-here
CLAUDE_MODEL=claude-haiku-4-5-20251001

# Optional: IMAP accounts (semicolon-separated)
IMAP_ACCOUNTS=gmail:user@gmail.com:app-password

# Optional: Your email addresses (for state engine thread tracking)
MY_EMAIL_ADDRESSES=user@gmail.com,user@company.com
```

### 4. Ingest Your Emails

```powershell
# From Outlook Desktop (must be open)
python run.py --ingest

# From PST files
python run.py --ingest --pst "C:\path\to\archive.pst"

# From IMAP only (skip Outlook)
python run.py --ingest --no-outlook

# Specify lookback period
python run.py --ingest --days 90

# Force full re-sync (ignore watermarks)
python run.py --ingest --full-sync
```

### 5. Start the Server

```powershell
python run.py --serve

# Or ingest then serve in one command
python run.py --ingest --serve
```

Open your browser to **http://localhost:5000**

---

## Use Cases & Examples

### Daily Email Triage

> "What emails need my attention today?"

> "Show me unread emails from this week"

> "Which emails did I send that haven't been replied to?"

> "What's in my inbox that's urgent?"

InboxAI's state engine classifies every thread as `needs_action`, `awaiting_response`, or `completed`, so it can answer these questions precisely.

### Finding Information

> "What did Sarah say about the Q4 budget?"

> "Find all emails mentioning PO-2024-1847"

> "Show me the latest thread about the server migration"

> "What attachments did Mike send about the proposal?"

The hybrid search pipeline catches both semantic matches (understanding meaning) and exact keyword matches (PO numbers, project codes, names).

### Deep Research

> Use the **Research** feature to get a comprehensive synthesis on a topic across all your emails with a timeline, thread summaries, and cited sources.

### Meeting Preparation

> Navigate to the **Meetings** tab to see your upcoming calendar. Click **Prep** on any meeting to generate an AI briefing that includes:
> - Background context from related emails
> - Key topics and open items
> - Recent decisions
> - Suggested action items

### Entity & Relationship Discovery

> Use the **Entity Map** to visualize who communicates about what. The interactive graph shows:
> - **People** — who's involved in which topics
> - **Topics** — key subjects across your inbox
> - **Organizations** — companies and teams mentioned
> - **Relationships** — co-mentions, shared threads, sentiment

### Fact Extraction

> Use **Extract Facts** in Settings to process emails with Claude and build a structured knowledge base of:
> - Commitments people have made (and deadlines)
> - Action items with assignees
> - Key entities and sentiment
> - Topic tags

Query extracted facts via the API:
```
GET /api/facts/commitments?person=Sarah
GET /api/facts/actions?assignee=me
```

### Analytics & Dashboard

> The **Dashboard** tab shows email volume over time, top senders, folder distribution, and key metrics (total emails, unread count, action items needed, awaiting responses).

---

## Automated Sync

Set up recurring email ingestion with Windows Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_scheduler.ps1
```

This creates a task named `InboxAI-EmailSync` that runs `python run.py --ingest` every 6 hours with incremental sync.

---

## Configuration Reference

### LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAMACPP_URL` | `http://localhost:8080` | llama.cpp server URL |
| `OLLAMA_MODEL` | `llama-3.1-8b-instruct` | Model name for local inference |
| `LLM_TEMPERATURE` | `0.3` | Response creativity (0.0–1.0) |
| `CLAUDE_API_KEY` | *(empty)* | Anthropic API key (enables Claude backend) |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Claude model ID |

### Email Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_LOOKBACK_DAYS` | `365` | How far back to ingest |
| `EMAIL_RETENTION_DAYS` | `365` | Auto-delete emails older than this |
| `OUTLOOK_FOLDERS` | `Inbox,Sent Items` | Outlook folders to sync |
| `IMAP_ACCOUNTS` | *(empty)* | IMAP accounts (`provider:email:password;...`) |
| `MY_EMAIL_ADDRESSES` | *(empty)* | Your addresses (for state engine) |
| `AUTOMATED_SENDERS` | *(empty)* | Bot senders to tag as automated |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `5000` | Listen port |
| `DB_PATH` | `~/.inboxai/chroma` | ChromaDB storage location |

### Recommended Models

| Model | VRAM | Best For |
|-------|------|----------|
| `llama3.2:3b` | 4GB | Good balance for consumer GPUs |
| `llama3.2:1b` | 2GB | Fastest, lower accuracy |
| `llama-3.1-8b-instruct` | 6GB | Higher quality responses |
| `mistral:7b` | 6GB | Strong general purpose |

---

## IMAP Setup Guide

### Gmail
1. Enable 2-Factor Authentication on your Google account
2. Generate an App Password: Google Account → Security → App Passwords
3. Add to `.env`:
   ```env
   IMAP_ACCOUNTS=gmail:yourname@gmail.com:your-app-password
   ```

### Yahoo Mail
1. Generate an App Password: Account Security → Generate app password
2. Add to `.env`:
   ```env
   IMAP_ACCOUNTS=yahoo:yourname@yahoo.com:your-app-password
   ```

### Multiple Accounts
Separate accounts with semicolons:
```env
IMAP_ACCOUNTS=gmail:work@gmail.com:pass1;yahoo:personal@yahoo.com:pass2
```

---

## API Reference

### Chat & Search

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send query, get AI response (supports `?stream=true`) |
| `/api/search` | POST | Direct email search with relevance scores |
| `/api/research` | POST | Deep topic research with synthesis |
| `/api/topic-map` | POST | Build topic connection map |
| `/api/entity-map` | POST | Build entity relationship graph |

### Email Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ingest` | POST | Start email ingestion |
| `/api/clear` | POST | Clear entire email database |
| `/api/email/open` | POST | Open email in Outlook or browser |

### Statistics & Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | LLM status, email count, Outlook availability |
| `/api/stats` | GET | Email statistics (counts, dates, folders) |
| `/api/analytics` | GET | Chart data (by date, sender, folder) |
| `/api/summary` | GET | Inbox summary (action needed, awaiting) |
| `/api/tasks` | GET | Categorized open action items |
| `/api/models` | GET | Available LLM models |

### Meetings & Calendar

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/meetings` | GET | Upcoming meetings (`?days=5`) |
| `/api/meetings/<index>/prep` | GET | AI meeting prep brief (supports streaming) |

### Fact Cards

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/extract` | POST | Extract fact cards from emails |
| `/api/facts/stats` | GET | Extraction statistics |
| `/api/facts/commitments` | GET | Commitments (`?person=name`) |
| `/api/facts/actions` | GET | Action items (`?assignee=name`) |

### Sync Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sync/status` | GET | Sync watermarks and stats |
| `/api/sync/history` | GET | Sync history log |

### Settings & Debug

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | GET/POST | View or update runtime settings |
| `/api/debug` | POST | Sample emails and test retrieval |
| `/api/debug/query` | POST | Debug a specific query's search results |

---

## Project Structure

```
email-rag-app/
├── run.py                      # CLI entry point (--ingest, --serve, --pst, etc.)
├── start_inboxai.bat            # Windows batch launcher
├── requirements.txt             # Python dependencies
├── .env.example                 # Configuration template
│
├── src/
│   ├── app.py                   # Flask web server & API routes
│   ├── config.py                # Environment configuration
│   ├── rag_engine.py            # RAG orchestration, research, meeting prep
│   ├── llm_client.py            # LLM backend (Ollama/llama.cpp + Claude API)
│   ├── vector_store.py          # ChromaDB storage & semantic search
│   ├── hybrid_search.py         # BM25 + semantic + RRF fusion pipeline
│   ├── bm25_index.py            # BM25 lexical index (rank-bm25)
│   ├── reranker.py              # Cross-encoder reranking (ms-marco)
│   ├── ingestion.py             # Email loading, batching, dedup
│   ├── email_preprocessor.py    # Boilerplate stripping, chunking
│   ├── attachment_extractor.py  # PDF/DOCX/XLSX/PPTX/TXT extraction
│   ├── outlook_connection.py    # Outlook Desktop COM connector
│   ├── imap_connection.py       # IMAP connector (Gmail, Yahoo, etc.)
│   ├── pst_parser.py            # PST/OST file parser
│   ├── calendar_connection.py   # Outlook Calendar via COM
│   ├── sync_state.py            # Incremental sync, watermarks, dedup
│   ├── state_engine.py          # Deterministic thread state tracking
│   ├── fact_extractor.py        # Claude-powered entity/intent extraction
│   ├── fact_cards.py            # Fact card data structures
│   ├── fact_store.py            # SQLite fact card storage
│   └── models.py                # Data models (EmailMessage, EmailThread, etc.)
│
├── templates/
│   └── index.html               # Single-page web UI
│
├── static/
│   ├── app.js                   # Frontend JavaScript
│   └── style.css                # Glassmorphism UI styles
│
└── scripts/
    └── setup_scheduler.ps1      # Windows Task Scheduler for auto-sync
```

---

## How It Works

### Search Pipeline

```
User Query
    ↓
Time Reference Parsing ("today" → date filter)
    ↓
┌─────────────────────┬──────────────────────┐
│  Semantic Search     │  BM25 Keyword Search │
│  (ChromaDB + MiniLM) │  (rank-bm25 index)   │
└─────────┬───────────┴──────────┬───────────┘
          ↓                      ↓
      Reciprocal Rank Fusion (RRF k=60)
          ↓
   Cross-Encoder Reranking (ms-marco-MiniLM)
          ↓
   Thread Expansion (full conversation context)
          ↓
   LLM Response Generation (with [SRC-N] citations)
```

### Email Processing

```
Email Source (Outlook/IMAP/PST)
    ↓
Deduplication (Message-ID + body hash)
    ↓
Preprocessing (boilerplate strip, signature removal)
    ↓
Chunking (fresh content | quoted replies | attachments)
    ↓
Embedding (sentence-transformers all-MiniLM-L6-v2)
    ↓
Storage (ChromaDB + BM25 index + metadata)
```

---

## Troubleshooting

### "Collection has 0 documents"
Run email ingestion first:
```powershell
python run.py --ingest
```

### LLM not responding
Check that your LLM backend is running:
```powershell
# For Ollama
ollama serve

# For llama.cpp
# Ensure server is running on configured port
curl http://localhost:8080/health
```

### Slow responses
- Use a smaller model (`llama3.2:3b` instead of 8B)
- Ensure GPU acceleration is active
- Check system RAM usage — ChromaDB needs memory for embeddings

### Outlook connection errors
- Ensure Outlook Desktop is open and running
- Run the script as the same user that's logged into Outlook
- COM connection requires Windows

### IMAP authentication failures
- Gmail/Yahoo require App Passwords (not your regular password)
- Enable 2FA first, then generate an App Password
- Check provider format: `gmail:email:apppassword`

---

## Privacy & Security

- **100% Local by Default** — All processing happens on your machine
- **No Telemetry** — No data sent to external services
- **Optional Cloud** — Claude API is opt-in and only sends email snippets for analysis
- **Local Storage** — Emails stored in ChromaDB at `~/.inboxai/chroma`, facts in SQLite
- **Credentials** — API keys and passwords stored only in your local `.env` file

---

## Cleanup

To remove all InboxAI data:
```powershell
# Delete database and indexed emails
Remove-Item -Recurse -Force "$env:USERPROFILE\.inboxai"

# Remove scheduled task (if created)
Unregister-ScheduledTask -TaskName "InboxAI-EmailSync" -Confirm:$false

# Remove Ollama model (optional)
ollama rm llama3.2:3b
```

---

## License

Private project. All rights reserved.
