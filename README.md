# InboxAI — Local Email Intelligence

A privacy-first email RAG application that uses local LLMs to answer questions about your inbox. All data stays on your machine.

## Features

- 📧 **Email Ingestion**: Import from live Outlook Desktop
- 🔍 **Semantic Search**: ChromaDB-powered vector search
- 🤖 **Local LLM**: Works with Ollama (GPU accelerated)
- 💬 **Chat Interface**: Natural language queries about your emails

## System Requirements

- **Windows 10/11** with Outlook Desktop installed
- **Python 3.10+**
- **8GB+ RAM** (24GB recommended)
- **GPU**: AMD RX 580 or similar (optional but recommended)

---

## Quick Start

### 1. Install Ollama

Download and install Ollama from: https://ollama.com/download

After installation, pull a model:
```powershell
ollama pull llama3.2:3b
```

### 2. Setup the Project

```powershell
# Extract the zip file and navigate to it
cd D:\email-rag-app

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Ingest Your Emails

Make sure Outlook is open, then run:
```powershell
python run.py --ingest
```

This will index emails from your Inbox and Sent Items.

### 4. Start the Server

```powershell
python run.py
```

### 5. Open the Web UI

Go to: http://localhost:5000

---

## Usage Examples

Ask questions like:
- "What emails need my attention today?"
- "Which emails did I send that haven't been replied to?"
- "Show me emails from John about the project"
- "Summarize my unread emails"

---

## Configuration

Create a `.env` file to customize settings:

```env
OLLAMA_MODEL=llama3.2:3b
EMAIL_LOOKBACK_DAYS=365
```

### Recommended Models

| Model | VRAM | Context | Best For |
|-------|------|---------|----------|
| `llama3.2:3b` | 4GB | 8K | Good balance (recommended) |
| `llama3.2:1b` | 2GB | 8K | Faster, less accurate |
| `mistral:7b` | 6GB | 8K | Higher quality |
| `phi3:mini` | 3GB | 4K | Smallest, limited context |

For your RX 580 (8GB VRAM), `llama3.2:3b` or `mistral:7b` work well.

---

## GPU Acceleration (AMD)

Ollama supports AMD GPUs via ROCm. Your RX 580 should work automatically.

Check if GPU is being used:
```powershell
ollama run llama3.2:3b "Hello"
# Should be fast (< 5 seconds)
```

---

## Troubleshooting

### "Collection has 0 documents"
Run email ingestion first:
```powershell
python run.py --ingest
```

### Ollama not responding
Make sure Ollama is running:
```powershell
ollama serve
```

### Slow responses
- Use a smaller model: `ollama pull llama3.2:1b`
- Check GPU is being used (responses should be fast)

### ChromaDB errors on startup
This is a known race condition - the app recovers automatically. Just ignore it.

---

## Project Structure

```
email-rag-app/
├── run.py              # Main entry point
├── requirements.txt    # Python dependencies
├── src/
│   ├── app.py          # Flask web server
│   ├── config.py       # Configuration
│   ├── rag_engine.py   # RAG orchestration
│   ├── llm_client.py   # Ollama integration
│   ├── vector_store.py # ChromaDB storage
│   ├── ingestion.py    # Email loading
│   └── outlook_connection.py  # Outlook COM
├── static/             # CSS & JavaScript
└── templates/          # HTML templates
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send query, get AI response |
| `/api/search` | POST | Direct email search |
| `/api/ingest` | POST | Start email ingestion |
| `/api/stats` | GET | Database statistics |
| `/api/health` | GET | Health check |

---

## Privacy

- **100% Local**: All processing on your machine
- **No Cloud**: Nothing sent to external servers
- **Your Data**: Emails stored locally in ChromaDB

---

## Cleanup

To remove everything:
```powershell
# Delete project
Remove-Item -Recurse -Force "D:\email-rag-app"

# Delete database
Remove-Item -Recurse -Force "$env:USERPROFILE\.inboxai"

# Remove Ollama model (optional)
ollama rm llama3.2:3b
```
