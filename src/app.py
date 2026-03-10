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
    days = request.args.get('days', 5, type=int)
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


@app.route('/api/entity-map', methods=['POST'])
def entity_map():
    """Build an entity relationship map showing people and topic connections."""
    data = request.json
    subject = data.get('subject', '').strip()

    if not subject:
        return jsonify({'error': 'Subject required'}), 400

    rag = get_rag_engine()
    result = rag.build_entity_map(subject)
    return jsonify(result)


@app.route('/api/extract', methods=['POST'])
def extract():
    """Extract fact cards from unprocessed emails using Claude."""
    data = request.json or {}
    limit = data.get('limit', 500)

    try:
        from fact_extractor import run_extraction
        result = run_extraction(limit=limit)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/facts/stats')
def fact_stats():
    """Get fact card extraction statistics."""
    try:
        from fact_store import get_fact_store
        store = get_fact_store()
        return jsonify(store.get_stats())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/facts/commitments')
def fact_commitments():
    """Get extracted commitments, optionally filtered by person."""
    person = request.args.get('person')
    try:
        from fact_store import get_fact_store
        store = get_fact_store()
        return jsonify({'commitments': store.get_commitments(person=person)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/facts/actions')
def fact_actions():
    """Get extracted action items, optionally filtered by assignee."""
    assignee = request.args.get('assignee')
    try:
        from fact_store import get_fact_store
        store = get_fact_store()
        return jsonify({'action_items': store.get_action_items(assignee=assignee)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sync/status')
def sync_status():
    """Get sync watermarks and overall sync stats."""
    try:
        from sync_state import get_sync_state
        sync = get_sync_state()
        return jsonify({
            'watermarks': sync.get_all_watermarks(),
            'stats': sync.get_stats(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sync/history')
def sync_history():
    """Get recent sync history log."""
    account = request.args.get('account')
    limit = request.args.get('limit', 20, type=int)
    try:
        from sync_state import get_sync_state
        sync = get_sync_state()
        return jsonify({
            'history': sync.get_sync_history(account_id=account, limit=limit),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    """Open an email in Outlook or return a web link for IMAP-sourced emails."""
    import urllib.parse

    data = request.json or {}
    message_id = data.get('message_id', '')
    subject = data.get('subject', '')
    sender = data.get('sender', '')
    source = data.get('source', '')

    # For IMAP-sourced emails, return a web link
    if source == 'gmail':
        # Build search using subject + from for reliable Gmail web search
        parts = []
        if subject:
            # Escape quotes in subject, truncate to avoid URL issues
            clean_subj = subject[:80].replace('"', '')
            parts.append(f'subject:"{clean_subj}"')
        if sender:
            parts.append(f'from:{sender}')
        query = urllib.parse.quote(' '.join(parts)) if parts else ''
        if query:
            return jsonify({'status': 'web', 'url': f'https://mail.google.com/mail/u/0/#search/{query}'})
        return jsonify({'error': 'No search criteria available'}), 400

    if source == 'yahoo':
        # Yahoo search — use subject + sender for best match
        parts = []
        if subject:
            parts.append(subject)
        if sender:
            parts.append(sender)
        query = urllib.parse.quote(' '.join(parts)) if parts else ''
        if query:
            return jsonify({'status': 'web', 'url': f'https://mail.yahoo.com/d/search/keyword={query}'})
        return jsonify({'error': 'No search criteria available'}), 400

    # Outlook — open via COM
    if not OUTLOOK_AVAILABLE:
        return jsonify({'error': 'Outlook not available'}), 400

    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        mail_item = None

        # Method 1: Try EntryID lookup (works if message_id is an EntryID)
        if message_id:
            try:
                mail_item = namespace.GetItemFromID(message_id)
            except Exception:
                logger.debug(f"EntryID lookup failed for {message_id[:30]}...")

        # Method 2: Search by Internet Message-ID using DASL filter
        if mail_item is None and message_id:
            dasl_filter = (
                f"@SQL=\"urn:schemas:mailheader:message-id\" = '{message_id}'"
                if '<' not in message_id else
                f"@SQL=\"urn:schemas:mailheader:message-id\" = '<{message_id}>'"
                if not message_id.startswith('<') else
                f"@SQL=\"urn:schemas:mailheader:message-id\" = '{message_id}'"
            )
            for folder_id in [6, 5]:  # Inbox, Sent
                try:
                    folder = namespace.GetDefaultFolder(folder_id)
                    items = folder.Items.Restrict(dasl_filter)
                    if items.Count > 0:
                        mail_item = items.GetFirst()
                        break
                except Exception:
                    continue

            # Also search configured folders
            if mail_item is None:
                for folder_name in config.OUTLOOK_FOLDERS:
                    try:
                        for store in namespace.Stores:
                            root = store.GetRootFolder()
                            folder = _find_folder(root, folder_name)
                            if folder:
                                items = folder.Items.Restrict(dasl_filter)
                                if items.Count > 0:
                                    mail_item = items.GetFirst()
                                    break
                        if mail_item:
                            break
                    except Exception:
                        continue

        # Method 3: Fallback — search by subject (use LIKE for partial matches)
        if mail_item is None and subject:
            safe_subject = subject.replace("'", "''").replace('"', '""')
            # Try exact match first, then LIKE
            for restrict_expr in [
                f"[Subject] = '{safe_subject}'",
                f"@SQL=\"urn:schemas:httpmail:subject\" LIKE '%{safe_subject}%'",
            ]:
                if mail_item:
                    break
                for folder_id in [6, 5]:
                    try:
                        folder = namespace.GetDefaultFolder(folder_id)
                        items = folder.Items.Restrict(restrict_expr)
                        if items.Count > 0:
                            mail_item = items.GetFirst()
                            break
                    except Exception:
                        continue

                if mail_item is None:
                    for folder_name in config.OUTLOOK_FOLDERS:
                        try:
                            for store in namespace.Stores:
                                root = store.GetRootFolder()
                                folder = _find_folder(root, folder_name)
                                if folder:
                                    items = folder.Items.Restrict(restrict_expr)
                                    if items.Count > 0:
                                        mail_item = items.GetFirst()
                                        break
                            if mail_item:
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


def _find_folder(parent, target_name):
    """Recursively search for a folder by name."""
    try:
        for folder in parent.Folders:
            if folder.Name.lower() == target_name.lower():
                return folder
            result = _find_folder(folder, target_name)
            if result:
                return result
    except:
        pass
    return None


def run_server():
    app.run(host=config.HOST, port=config.PORT, debug=True, threaded=True)


if __name__ == '__main__':
    run_server()
