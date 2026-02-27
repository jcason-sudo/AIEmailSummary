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
from llm_client import get_ollama_client
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
    
    if not message:
        return jsonify({'error': 'Message required'}), 400
    
    rag = get_rag_engine()
    
    if data.get('stream'):
        def generate():
            for item in rag.query_stream(message):
                yield f"data: {json.dumps(item)}\n\n"
            yield "data: [DONE]\n\n"
        
        return Response(generate(), mimetype='text/event-stream')
    else:
        result = rag.query(message)
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

    try:
        results = run_ingestion(
            pst_paths=[Path(p) for p in pst_paths] if pst_paths else None,
            include_outlook=include_outlook,
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
    """Get next business day meetings from Outlook Calendar."""
    rag = get_rag_engine()
    return jsonify(rag.get_meetings())


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


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get current settings."""
    llm = get_ollama_client()
    return jsonify({
        'llm': {
            'backend': 'ollama',
            'model': llm.model,
            'temperature': llm.temperature
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
    llm = get_ollama_client()
    updated = []

    if 'temperature' in data:
        llm.set_temperature(float(data['temperature']))
        updated.append('temperature')

    if 'model' in data:
        llm.set_model(data['model'])
        updated.append('model')

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


def run_server():
    app.run(host=config.HOST, port=config.PORT, debug=True, threaded=True)


if __name__ == '__main__':
    run_server()
