"""
Flask web application.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
import json

sys.path.insert(0, str(Path(__file__).parent))

import config
from rag_engine import get_rag_engine
from vector_store import get_vector_store
from ingestion import run_ingestion
from llm_client import get_ollama_client, get_llm_client
from outlook_connection import OUTLOOK_AVAILABLE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            template_folder='../templates',
            static_folder='../static')
CORS(app)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    llm = get_ollama_client()
    store = get_vector_store()
    
    return jsonify({
        'llm_connected': llm.is_available(),
        'llm_model': config.OLLAMA_MODEL,
        'email_count': store._collection.count(),
        'outlook_available': OUTLOOK_AVAILABLE
    })


@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    message = data.get('message', '').strip()
    backend = data.get('backend', 'claude')  # "claude" or "local"

    if not message:
        return jsonify({'error': 'Message required'}), 400

    rag = get_rag_engine()

    if data.get('stream'):
        def generate():
            for item in rag.query_stream(message, backend=backend):
                yield f"data: {json.dumps(item)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(generate(), mimetype='text/event-stream')
    else:
        result = rag.query(message, backend=backend)
        return jsonify(result)


@app.route('/api/summary')
def summary():
    rag = get_rag_engine()
    return jsonify(rag.get_summary())


@app.route('/api/stats')
def stats():
    store = get_vector_store()
    return jsonify(store.get_stats())


@app.route('/api/search', methods=['POST'])
def search():
    data = request.json
    query = data.get('query', '').strip()
    limit = data.get('limit', 20)
    
    if not query:
        return jsonify({'error': 'Query required'}), 400
    
    store = get_vector_store()
    results = store.search(query, n_results=limit)
    
    return jsonify({
        'results': [
            {
                'sender': r['metadata'].get('sender', ''),
                'subject': r['metadata'].get('subject', ''),
                'date': r['metadata'].get('date', ''),
                'preview': r['document'][:200],
                'relevance': round(r['relevance'] * 100, 1)
            }
            for r in results
        ]
    })


@app.route('/api/ingest', methods=['POST'])
@app.route('/api/ingest/start', methods=['POST'])
def ingest():
    data = request.json or {}
    days = data.get('days') or data.get('days_back') or config.EMAIL_LOOKBACK_DAYS
    pst_paths = data.get('pst_paths', [])
    include_outlook = data.get('include_outlook', True)
    include_imap = data.get('include_imap', True)

    try:
        results = run_ingestion(
            pst_paths=[Path(p) for p in pst_paths] if pst_paths else None,
            include_outlook=include_outlook,
            include_imap=include_imap,
            days_back=int(days)
        )
        return jsonify(results)
    except Exception as e:
        logger.error(f"Ingestion error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/clear', methods=['POST'])
@app.route('/api/clear-database', methods=['POST'])
def clear():
    store = get_vector_store()
    store.clear()
    return jsonify({'status': 'cleared'})


@app.route('/api/models')
def models():
    llm = get_ollama_client()
    return jsonify({'models': llm.list_models()})


@app.route('/api/tasks')
def tasks():
    """Get categorized open action items with thread context."""
    rag = get_rag_engine()
    return jsonify(rag.get_tasks())


@app.route('/api/meetings')
def meetings():
    """Get upcoming meetings from Outlook Calendar."""
    days = request.args.get('days', 7, type=int)
    rag = get_rag_engine()
    return jsonify(rag.get_meetings(days=days))


@app.route('/api/meetings/<int:index>/prep')
def meeting_prep(index):
    """Get AI-generated meeting prep brief for a specific meeting."""
    rag = get_rag_engine()
    meetings_data = rag.get_meetings()
    meeting_list = meetings_data.get('meetings', [])

    if index < 0 or index >= len(meeting_list):
        return jsonify({'error': 'Meeting not found'}), 404

    meeting = meeting_list[index]

    # Check if streaming requested
    if request.args.get('stream'):
        def generate():
            for item in rag.prepare_for_meeting_stream(meeting):
                yield f"data: {json.dumps(item)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(generate(), mimetype='text/event-stream')
    else:
        result = rag.prepare_for_meeting(meeting)
        return jsonify(result)


@app.route('/api/research', methods=['POST'])
def research():
    """Deep research on a topic across all related emails."""
    data = request.json
    topic = data.get('topic', '').strip()
    backend = data.get('backend', 'claude')

    if not topic:
        return jsonify({'error': 'Topic required'}), 400

    rag = get_rag_engine()

    if data.get('stream'):
        def generate():
            for item in rag.deep_research_stream(topic, backend=backend):
                yield f"data: {json.dumps(item)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(generate(), mimetype='text/event-stream')
    else:
        result = rag.deep_research(topic, backend=backend)
        return jsonify(result)


@app.route('/api/topic-map', methods=['POST'])
def topic_map():
    """Build a topic map showing connections between emails and people."""
    data = request.json
    topic = data.get('topic', '').strip()

    if not topic:
        return jsonify({'error': 'Topic required'}), 400

    rag = get_rag_engine()
    result = rag.build_topic_map(topic)
    return jsonify(result)


@app.route('/api/analytics')
def analytics():
    """Get email analytics for charts."""
    store = get_vector_store()
    return jsonify(store.get_analytics())


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get current settings."""
    llm = get_ollama_client()

    # Check which backends are available
    backends = [{'id': 'local', 'name': 'Local GPU (llama.cpp)', 'available': True}]
    claude_available = bool(config.CLAUDE_API_KEY)
    backends.append({'id': 'claude', 'name': 'Claude API (Haiku)', 'available': claude_available})

    return jsonify({
        'llm': {
            'backend': 'local',
            'model': llm.model,
            'temperature': llm.temperature,
            'backends': backends
        },
        'email': {
            'lookback_days': config.EMAIL_LOOKBACK_DAYS,
            'folders': config.OUTLOOK_FOLDERS
        },
        'outlook_available': OUTLOOK_AVAILABLE
    })


@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update settings at runtime."""
    data = request.json or {}
    updated = []

    backend = data.get('backend', 'local')

    if 'temperature' in data:
        # Apply temperature to both backends so it persists across switches
        temp = float(data['temperature'])
        get_ollama_client().set_temperature(temp)
        try:
            get_llm_client('claude').set_temperature(temp)
        except ValueError:
            pass  # Claude not configured
        updated.append('temperature')

    if 'model' in data:
        # Only apply model to the local backend — Claude model is fixed via config
        if backend == 'local':
            get_ollama_client().set_model(data['model'])
        updated.append('model')

    if 'backend' in data:
        updated.append('backend')

    return jsonify({'status': 'updated', 'updated_fields': updated})


@app.route('/api/debug')
def debug():
    """Debug endpoint - shows sample emails and tests retrieval."""
    store = get_vector_store()
    
    # Get sample emails
    samples = store.debug_sample(5)
    
    # Test a simple search
    test_results = store.search("email", n_results=3)
    
    return jsonify({
        'total_emails': store._collection.count(),
        'sample_emails': samples,
        'test_search_results': len(test_results),
        'test_search_preview': [
            {
                'subject': r['metadata'].get('subject', ''),
                'sender': r['metadata'].get('sender', ''),
                'document_length': len(r.get('document', '')),
                'document_preview': r.get('document', '')[:300],
                'relevance': r.get('relevance', 0)
            }
            for r in test_results
        ]
    })


@app.route('/api/debug/query', methods=['POST'])
def debug_query():
    """Debug a specific query - shows what would be sent to the LLM."""
    data = request.json
    query = data.get('query', 'show me recent emails')
    
    store = get_vector_store()
    rag = get_rag_engine()
    
    # Get search results
    results = store.search(query, n_results=10)
    
    # Format the context that would be sent to LLM
    from llm_client import get_ollama_client
    llm = get_ollama_client()
    context = llm._format_email_context(results)
    
    return jsonify({
        'query': query,
        'emails_found': len(results),
        'context_length': len(context),
        'context_preview': context[:3000] + ('...' if len(context) > 3000 else ''),
        'results': [
            {
                'subject': r['metadata'].get('subject', ''),
                'sender': r['metadata'].get('sender', ''),
                'date': r['metadata'].get('date', ''),
                'direction': r['metadata'].get('direction', ''),
                'is_replied': r['metadata'].get('is_replied', ''),
                'document_length': len(r.get('document', '')),
                'document_preview': r.get('document', '')[:500],
            }
            for r in results
        ]
    })


@app.route('/api/email/open', methods=['POST'])
def open_email():
    """Open an email in Outlook using EntryID or subject+sender search."""
    data = request.json or {}
    message_id = data.get('message_id', '')
    subject = data.get('subject', '')
    sender = data.get('sender', '')

    if not OUTLOOK_AVAILABLE:
        return jsonify({'error': 'Outlook not available'}), 400

    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        mail_item = None

        # Try EntryID first
        if message_id:
            try:
                mail_item = namespace.GetItemFromID(message_id)
            except Exception:
                logger.debug(f"EntryID lookup failed for {message_id[:20]}...")

        # Fallback: search by subject + sender in Inbox and Sent Items
        if mail_item is None and subject:
            for folder_id in [6, 5]:  # Inbox=6, Sent=5
                try:
                    folder = namespace.GetDefaultFolder(folder_id)
                    items = folder.Items
                    safe_subject = subject.replace("'", "''")
                    items = items.Restrict(f"[Subject] = '{safe_subject}'")
                    if items.Count > 0:
                        mail_item = items.GetFirst()
                        break
                except Exception:
                    continue

        if mail_item:
            mail_item.Display()
            return jsonify({'status': 'opened', 'subject': subject})
        else:
            return jsonify({'error': 'Email not found in Outlook'}), 404

    except Exception as e:
        logger.error(f"Failed to open email: {e}")
        return jsonify({'error': str(e)}), 500


def run_server():
    app.run(host=config.HOST, port=config.PORT, debug=True, threaded=True)


if __name__ == '__main__':
    run_server()
