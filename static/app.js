/**
 * InboxAI — Email Intelligence Frontend
 * Handles chat interface, API communication, and UI state
 */

class InboxAI {
    constructor() {
        this.apiBase = '';
        this.conversationHistory = [];
        this.isStreaming = false;

        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
        this.checkHealth();
        this.loadDashboard();
        this.loadSettings();

        // Auto-resize textarea
        this.setupTextareaAutoResize();
    }

    bindElements() {
        // Navigation
        this.navItems = document.querySelectorAll('.nav-item');
        this.views = document.querySelectorAll('.view');

        // Status
        this.statusIndicator = document.getElementById('status-indicator');
        this.emailCount = document.getElementById('email-count');

        // Chat
        this.chatMessages = document.getElementById('chat-messages');
        this.chatInput = document.getElementById('chat-input');
        this.sendButton = document.getElementById('send-button');
        this.quickActions = document.querySelectorAll('.quick-action');

        // Dashboard
        this.refreshDashboard = document.getElementById('refresh-dashboard');
        this.actionNeededList = document.getElementById('action-needed-list');
        this.awaitingResponseList = document.getElementById('awaiting-response-list');

        // Tasks
        this.refreshTasks = document.getElementById('refresh-tasks');
        this.tasksNeedsAction = document.getElementById('tasks-needs-action');
        this.tasksAwaitingResponse = document.getElementById('tasks-awaiting-response');

        // Settings
        this.llmBackend = document.getElementById('llm-backend');
        this.llmModel = document.getElementById('llm-model');
        this.llmTemperature = document.getElementById('llm-temperature');
        this.temperatureValue = document.getElementById('temperature-value');
        this.lookbackDays = document.getElementById('lookback-days');
        this.startIngestion = document.getElementById('start-ingestion');
        this.clearDatabase = document.getElementById('clear-database');
        this.ingestionStatus = document.getElementById('ingestion-status');
        this.saveLlmSettings = document.getElementById('save-llm-settings');
    }

    bindEvents() {
        // Navigation
        this.navItems.forEach(item => {
            item.addEventListener('click', () => this.switchView(item.dataset.view));
        });

        // Chat input
        this.chatInput.addEventListener('input', () => this.updateSendButton());
        this.chatInput.addEventListener('keydown', (e) => this.handleInputKeydown(e));
        this.sendButton.addEventListener('click', () => this.sendMessage());

        // Quick actions
        this.quickActions.forEach(action => {
            action.addEventListener('click', () => {
                this.chatInput.value = action.dataset.query;
                this.sendMessage();
            });
        });

        // Dashboard
        this.refreshDashboard?.addEventListener('click', () => this.loadDashboard());

        // Tasks
        this.refreshTasks?.addEventListener('click', () => this.loadTasks());

        // Settings
        this.llmBackend?.addEventListener('change', () => this.loadModels());
        this.llmTemperature?.addEventListener('input', () => {
            this.temperatureValue.textContent = this.llmTemperature.value;
        });
        this.startIngestion?.addEventListener('click', () => this.runIngestion());
        this.clearDatabase?.addEventListener('click', () => this.clearDB());
        this.saveLlmSettings?.addEventListener('click', () => this.saveSettings());
    }

    setupTextareaAutoResize() {
        this.chatInput.addEventListener('input', () => {
            this.chatInput.style.height = 'auto';
            this.chatInput.style.height = Math.min(this.chatInput.scrollHeight, 200) + 'px';
        });
    }

    // Navigation
    switchView(viewName) {
        this.navItems.forEach(item => {
            item.classList.toggle('active', item.dataset.view === viewName);
        });

        this.views.forEach(view => {
            view.classList.toggle('active', view.id === `${viewName}-view`);
        });

        // Load data for specific views
        if (viewName === 'dashboard') {
            this.loadDashboard();
        } else if (viewName === 'tasks') {
            this.loadTasks();
        } else if (viewName === 'settings') {
            this.loadSettings();
        }
    }

    // Health Check
    async checkHealth() {
        try {
            const response = await fetch(`${this.apiBase}/api/health`);
            const data = await response.json();

            if (data.llm_connected) {
                this.statusIndicator.classList.add('connected');
                this.statusIndicator.classList.remove('error');
                this.statusIndicator.querySelector('.status-text').textContent =
                    `${data.llm_backend || 'Ollama'} connected`;
            } else {
                this.statusIndicator.classList.remove('connected');
                this.statusIndicator.classList.add('error');
                this.statusIndicator.querySelector('.status-text').textContent =
                    'LLM disconnected';
            }

            // Update email count
            const countEl = this.emailCount.querySelector('.count');
            countEl.textContent = data.email_count.toLocaleString();

        } catch (error) {
            console.error('Health check failed:', error);
            this.statusIndicator.classList.remove('connected');
            this.statusIndicator.classList.add('error');
            this.statusIndicator.querySelector('.status-text').textContent = 'Server offline';
        }
    }

    // Chat
    updateSendButton() {
        this.sendButton.disabled = !this.chatInput.value.trim();
    }

    handleInputKeydown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (this.chatInput.value.trim()) {
                this.sendMessage();
            }
        }
    }

    async sendMessage() {
        const message = this.chatInput.value.trim();
        if (!message || this.isStreaming) return;

        // Clear input
        this.chatInput.value = '';
        this.chatInput.style.height = 'auto';
        this.updateSendButton();

        // Hide welcome message
        const welcome = this.chatMessages.querySelector('.welcome-message');
        if (welcome) {
            welcome.remove();
        }

        // Add user message
        this.addMessage('user', message);

        // Add loading indicator
        const loadingEl = this.addMessage('assistant', '', true);

        this.isStreaming = true;

        try {
            const response = await fetch(`${this.apiBase}/api/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, stream: true })
            });

            // Handle streaming response
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';
            let sources = [];

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') continue;

                        try {
                            const parsed = JSON.parse(data);

                            if (parsed.type === 'chunk') {
                                fullContent += parsed.content;
                                this.updateMessageContent(loadingEl, fullContent);
                            } else if (parsed.type === 'sources') {
                                sources = parsed.content;
                            }
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            }

            // Finalize message
            loadingEl.classList.remove('loading');
            this.updateMessageContent(loadingEl, fullContent, sources);

        } catch (error) {
            console.error('Chat error:', error);
            loadingEl.classList.remove('loading');
            this.updateMessageContent(loadingEl,
                'Sorry, I encountered an error processing your request. Please check that the LLM server is running.');
        }

        this.isStreaming = false;
        this.scrollToBottom();
    }

    addMessage(role, content, isLoading = false) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${role}${isLoading ? ' loading' : ''}`;

        const avatarSvg = role === 'user'
            ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
            : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

        messageEl.innerHTML = `
            <div class="message-avatar">${avatarSvg}</div>
            <div class="message-content">
                <div class="message-bubble">
                    ${isLoading
                        ? '<div class="typing-indicator"><span></span><span></span><span></span></div>'
                        : this.formatContent(content)
                    }
                </div>
            </div>
        `;

        this.chatMessages.appendChild(messageEl);
        this.scrollToBottom();

        return messageEl;
    }

    updateMessageContent(messageEl, content, sources = []) {
        const bubble = messageEl.querySelector('.message-bubble');
        bubble.innerHTML = this.formatContent(content);

        // Add sources if available
        if (sources.length > 0) {
            // Group sources by conversation_id for thread display
            const threaded = {};
            const standalone = [];
            for (const s of sources) {
                if (s.conversation_id) {
                    if (!threaded[s.conversation_id]) {
                        threaded[s.conversation_id] = [];
                    }
                    threaded[s.conversation_id].push(s);
                } else {
                    standalone.push(s);
                }
            }

            const sourcesEl = document.createElement('div');
            sourcesEl.className = 'message-sources';

            let html = '';

            // Show threaded sources
            for (const [convId, threadSources] of Object.entries(threaded)) {
                html += `<span class="source-tag thread-tag">
                    <span class="thread-icon">&#x1f4e7;</span>
                    ${this.escapeHtml(threadSources[0].subject || 'Thread')}
                    <span class="thread-count">${threadSources.length} msgs</span>
                </span>`;
            }

            // Show standalone sources
            for (const s of standalone.slice(0, 5)) {
                html += `<span class="source-tag">
                    ${this.escapeHtml(s.sender || 'Unknown')}
                    ${s.relevance ? `<span class="relevance">${s.relevance}%</span>` : ''}
                </span>`;
            }

            sourcesEl.innerHTML = html;
            messageEl.querySelector('.message-content').appendChild(sourcesEl);
        }
    }

    formatContent(content) {
        if (!content) return '';

        // Basic markdown-like formatting
        let html = this.escapeHtml(content);

        // Bold
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

        // Code inline
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        // Lists (simple)
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

        return html;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    scrollToBottom() {
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    // Dashboard
    async loadDashboard() {
        try {
            // Load stats
            const statsResponse = await fetch(`${this.apiBase}/api/stats`);
            const stats = await statsResponse.json();

            document.getElementById('stat-total').textContent =
                (stats.total_emails || 0).toLocaleString();
            document.getElementById('stat-unread').textContent =
                (stats.unread || 0).toLocaleString();
            document.getElementById('stat-sent').textContent =
                (stats.sent || 0).toLocaleString();
            document.getElementById('stat-flagged').textContent =
                (stats.flagged || 0).toLocaleString();

            // Load summary
            const summaryResponse = await fetch(`${this.apiBase}/api/summary`);
            const summary = await summaryResponse.json();

            // Action needed list
            if (summary.action_needed && summary.action_needed.length > 0) {
                this.actionNeededList.innerHTML = summary.action_needed.map(email => `
                    <div class="email-item">
                        <div class="sender">${this.escapeHtml(email.sender)}</div>
                        <div class="subject">${this.escapeHtml(email.subject)}</div>
                        <div class="date">${this.formatDate(email.date)}</div>
                    </div>
                `).join('');
            } else {
                this.actionNeededList.innerHTML =
                    '<div class="empty-placeholder">No emails needing action</div>';
            }

            // Awaiting response list
            if (summary.awaiting_response && summary.awaiting_response.length > 0) {
                this.awaitingResponseList.innerHTML = summary.awaiting_response.map(email => `
                    <div class="email-item">
                        <div class="sender">To: ${this.escapeHtml(email.recipient)}</div>
                        <div class="subject">${this.escapeHtml(email.subject)}</div>
                        <div class="date">${this.formatDate(email.date)}</div>
                    </div>
                `).join('');
            } else {
                this.awaitingResponseList.innerHTML =
                    '<div class="empty-placeholder">No emails awaiting response</div>';
            }

        } catch (error) {
            console.error('Dashboard load error:', error);
            this.actionNeededList.innerHTML =
                '<div class="empty-placeholder">Error loading data</div>';
            this.awaitingResponseList.innerHTML =
                '<div class="empty-placeholder">Error loading data</div>';
        }
    }

    // Tasks
    async loadTasks() {
        try {
            const response = await fetch(`${this.apiBase}/api/tasks`);
            const data = await response.json();

            // Update stats
            const s = data.summary || {};
            document.getElementById('stat-needs-action').textContent =
                (s.needs_action_count || 0).toLocaleString();
            document.getElementById('stat-awaiting').textContent =
                (s.awaiting_response_count || 0).toLocaleString();
            document.getElementById('stat-deadlines').textContent =
                (s.with_deadlines || 0).toLocaleString();
            document.getElementById('stat-questions').textContent =
                (s.with_questions || 0).toLocaleString();

            // Needs action list
            if (data.needs_action && data.needs_action.length > 0) {
                this.tasksNeedsAction.innerHTML = data.needs_action.map(item => `
                    <div class="email-item">
                        <div class="sender">
                            ${this.escapeHtml(item.sender_name || item.sender)}
                            ${item.message_count > 1 ? `<span class="thread-badge">${item.message_count} msgs</span>` : ''}
                        </div>
                        <div class="subject">
                            ${this.escapeHtml(item.subject)}
                            ${(item.tags || []).map(t => `<span class="task-tag tag-${t}">${t}</span>`).join('')}
                        </div>
                        <div class="date">${this.formatDate(item.date)}</div>
                    </div>
                `).join('');
            } else {
                this.tasksNeedsAction.innerHTML =
                    '<div class="empty-placeholder">No items needing your action</div>';
            }

            // Awaiting response list
            if (data.awaiting_response && data.awaiting_response.length > 0) {
                this.tasksAwaitingResponse.innerHTML = data.awaiting_response.map(item => `
                    <div class="email-item">
                        <div class="sender">
                            ${this.escapeHtml(item.sender_name || item.sender)}
                            ${item.message_count > 1 ? `<span class="thread-badge">${item.message_count} msgs</span>` : ''}
                        </div>
                        <div class="subject">${this.escapeHtml(item.subject)}</div>
                        <div class="date">${this.formatDate(item.date)}</div>
                    </div>
                `).join('');
            } else {
                this.tasksAwaitingResponse.innerHTML =
                    '<div class="empty-placeholder">No emails awaiting response</div>';
            }

        } catch (error) {
            console.error('Tasks load error:', error);
            if (this.tasksNeedsAction) {
                this.tasksNeedsAction.innerHTML =
                    '<div class="empty-placeholder">Error loading tasks</div>';
            }
            if (this.tasksAwaitingResponse) {
                this.tasksAwaitingResponse.innerHTML =
                    '<div class="empty-placeholder">Error loading tasks</div>';
            }
        }
    }

    formatDate(dateStr) {
        if (!dateStr) return '';
        try {
            const date = new Date(dateStr);
            const now = new Date();
            const diff = now - date;

            if (diff < 86400000) {
                // Today
                return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } else if (diff < 604800000) {
                // This week
                return date.toLocaleDateString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
            } else {
                return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
            }
        } catch {
            return dateStr;
        }
    }

    // Settings
    async loadSettings() {
        try {
            const response = await fetch(`${this.apiBase}/api/settings`);
            const settings = await response.json();

            this.llmBackend.value = settings.llm?.backend || 'ollama';
            this.llmTemperature.value = settings.llm?.temperature || 0.3;
            this.temperatureValue.textContent = settings.llm?.temperature || 0.3;
            this.lookbackDays.value = settings.email?.lookback_days || 365;

            await this.loadModels();

            // Select current model after models are loaded
            if (settings.llm?.model) {
                this.llmModel.value = settings.llm.model;
            }

        } catch (error) {
            console.error('Settings load error:', error);
        }
    }

    async loadModels() {
        try {
            const response = await fetch(`${this.apiBase}/api/models`);
            const data = await response.json();

            if (data.models && data.models.length > 0) {
                this.llmModel.innerHTML = data.models.map(model =>
                    `<option value="${model}">${model}</option>`
                ).join('');
            } else {
                this.llmModel.innerHTML = '<option value="">No models found</option>';
            }
        } catch (error) {
            console.error('Models load error:', error);
            this.llmModel.innerHTML = '<option value="">Error loading models</option>';
        }
    }

    async saveSettings() {
        const model = this.llmModel.value;
        const temperature = parseFloat(this.llmTemperature.value);

        try {
            const response = await fetch(`${this.apiBase}/api/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model, temperature })
            });

            const result = await response.json();

            if (result.status === 'updated') {
                this.saveLlmSettings.textContent = 'Saved!';
                setTimeout(() => {
                    this.saveLlmSettings.innerHTML = `
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                            <polyline points="17 21 17 13 7 13 7 21"/>
                            <polyline points="7 3 7 8 15 8"/>
                        </svg>
                        Save LLM Settings
                    `;
                }, 2000);
            }
        } catch (error) {
            console.error('Save settings error:', error);
            alert('Failed to save settings');
        }
    }

    async runIngestion() {
        if (!confirm('Start email ingestion? This may take a while for large mailboxes.')) {
            return;
        }

        this.startIngestion.disabled = true;
        this.ingestionStatus.innerHTML = '<span class="status">Ingesting emails...</span>';

        try {
            const response = await fetch(`${this.apiBase}/api/ingest/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    days_back: parseInt(this.lookbackDays.value) || 365,
                    include_outlook: true
                })
            });

            const result = await response.json();

            if (result.error) {
                this.ingestionStatus.innerHTML = `
                    <span class="status" style="color: var(--color-error)">Error: ${this.escapeHtml(result.error)}</span>
                `;
            } else {
                const total = (result.pst_emails || 0) + (result.outlook_emails || 0);
                this.ingestionStatus.innerHTML = `
                    <span class="status">Complete! Indexed ${total} emails</span>
                `;
            }

            // Refresh health check
            this.checkHealth();

        } catch (error) {
            console.error('Ingestion error:', error);
            this.ingestionStatus.innerHTML = `
                <span class="status" style="color: var(--color-error)">Ingestion failed</span>
            `;
        }

        this.startIngestion.disabled = false;
    }

    async clearDB() {
        if (!confirm('Clear all indexed emails? This cannot be undone.')) {
            return;
        }

        try {
            await fetch(`${this.apiBase}/api/clear-database`, { method: 'POST' });
            this.checkHealth();
            alert('Database cleared successfully');
        } catch (error) {
            console.error('Clear database error:', error);
            alert('Failed to clear database');
        }
    }
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    window.app = new InboxAI();
});
