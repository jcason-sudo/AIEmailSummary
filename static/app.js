/**
 * InboxAI — Email Intelligence Frontend
 * Handles chat interface, API communication, and UI state
 */

class InboxAI {
    constructor() {
        this.apiBase = '';
        this.conversationHistory = [];
        this.isStreaming = false;
        this.backend = localStorage.getItem('inboxai-backend') || 'claude';

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

        // Meetings
        this.refreshMeetings = document.getElementById('refresh-meetings');
        this.meetingsDate = document.getElementById('meetings-date');
        this.meetingsList = document.getElementById('meetings-list');
        this.meetingPrepContainer = document.getElementById('meeting-prep-container');
        this.meetingPrepContent = document.getElementById('meeting-prep-content');
        this.meetingPrepSources = document.getElementById('meeting-prep-sources');
        this.prepMeetingTitle = document.getElementById('prep-meeting-title');

        // Research
        this.researchTopic = document.getElementById('research-topic');
        this.startResearchBtn = document.getElementById('start-research');
        this.researchStats = document.getElementById('research-stats');
        this.researchSynthesis = document.getElementById('research-synthesis');
        this.researchSynthesisContent = document.getElementById('research-synthesis-content');
        this.researchTimelineContainer = document.getElementById('research-timeline-container');
        this.researchTimeline = document.getElementById('research-timeline');
        this.researchThreadsContainer = document.getElementById('research-threads-container');
        this.researchThreads = document.getElementById('research-threads');
        this.researchTabs = document.getElementById('research-tabs');

        // Entity Map
        this.entityMapSubject = document.getElementById('entity-map-subject');
        this.startEntityMapBtn = document.getElementById('start-entity-map');
        this.entityMapStats = document.getElementById('entity-map-stats');
        this.entityMapLegend = document.getElementById('entity-map-legend');
        this.entityMapGraph = document.getElementById('entity-map-graph');
        this.entityMapDetail = document.getElementById('entity-map-detail');
        this.entityMapDetailContent = document.querySelector('#entity-map-detail .detail-content');
        this.entityMapTooltip = document.getElementById('entity-map-tooltip');
        this.entityMapFilters = document.getElementById('entity-map-filters');
        this.entityMapData = null;
        this.entityMapNetwork = null;
        this.entityMapNodeDataSet = null;
        this.entityMapEdgeDataSet = null;
        this._entityNodeMap = null;
        this.activeFilters = { type: 'all', sentiment: 'all' };

        // Charts
        this.charts = {};

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

        // Meetings
        this.refreshMeetings?.addEventListener('click', () => this.loadMeetings());

        // Research
        this.startResearchBtn?.addEventListener('click', () => this.startResearch());
        this.researchTopic?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.startResearch();
        });

        // Research tabs
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const tab = btn.dataset.tab;
                document.getElementById('research-tab-analysis').style.display = tab === 'analysis' ? '' : 'none';
                document.getElementById('research-tab-topicmap').style.display = tab === 'topicmap' ? '' : 'none';
            });
        });

        // Entity Map
        this.startEntityMapBtn?.addEventListener('click', () => this.fetchEntityMap());
        this.entityMapSubject?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.fetchEntityMap();
        });

        // Entity Map filter buttons
        document.querySelectorAll('.filter-btn[data-filter]').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.filter-group').querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.activeFilters.type = btn.dataset.filter;
                this.applyEntityMapFilters();
            });
        });
        document.querySelectorAll('.filter-btn[data-sentiment]').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.filter-group').querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.activeFilters.sentiment = btn.dataset.sentiment;
                this.applyEntityMapFilters();
            });
        });
        document.getElementById('entity-detail-close')?.addEventListener('click', () => {
            if (this.entityMapDetail) this.entityMapDetail.style.display = 'none';
        });

        // Settings
        this.llmBackend?.addEventListener('change', () => {
            this.backend = this.llmBackend.value;
            localStorage.setItem('inboxai-backend', this.backend);
            this.loadModels();
            this.checkHealth();
        });
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
        // Fade out current view
        const currentView = document.querySelector('.view.active');
        if (currentView) {
            currentView.style.opacity = '0';
            currentView.style.transition = 'opacity 0.15s ease';
        }

        setTimeout(() => {
            this.navItems.forEach(item => {
                item.classList.toggle('active', item.dataset.view === viewName);
            });

            this.views.forEach(view => {
                const isActive = view.id === `${viewName}-view`;
                view.classList.toggle('active', isActive);
                if (isActive) {
                    view.style.opacity = '0';
                    requestAnimationFrame(() => {
                        view.style.transition = 'opacity 0.15s ease';
                        view.style.opacity = '1';
                    });
                }
            });

            // Load data for specific views
            if (viewName === 'dashboard') {
                this.loadDashboard();
            } else if (viewName === 'meetings') {
                this.loadMeetings();
            } else if (viewName === 'settings') {
                this.loadSettings();
            }
        }, 150);
    }

    // Health Check
    async checkHealth() {
        try {
            const response = await fetch(`${this.apiBase}/api/health`);
            const data = await response.json();

            const backendLabel = this.backend === 'claude' ? 'Claude API' : 'llama.cpp';
            if (data.llm_connected) {
                this.statusIndicator.classList.add('connected');
                this.statusIndicator.classList.remove('error');
                this.statusIndicator.querySelector('.status-text').textContent =
                    `${backendLabel} connected`;
            } else {
                this.statusIndicator.classList.remove('connected');
                this.statusIndicator.classList.add('error');
                this.statusIndicator.querySelector('.status-text').textContent =
                    this.backend === 'claude' ? 'Claude API (no key)' : 'LLM disconnected';
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
                body: JSON.stringify({ message, stream: true, backend: this.backend })
            });

            // Handle streaming response
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';
            let refMap = {};

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
                            } else if (parsed.type === 'ref_map') {
                                refMap = parsed.content;
                            }
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            }

            // Finalize message with ref_map for clickable citations
            loadingEl.classList.remove('loading');
            this.updateMessageContent(loadingEl, fullContent, [], refMap);

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

    updateMessageContent(messageEl, content, sources = [], refMap = {}) {
        const bubble = messageEl.querySelector('.message-bubble');
        messageEl._refMap = refMap;
        bubble.innerHTML = this.formatContent(content, refMap);
    }

    formatContent(content, refMap = {}) {
        if (!content) return '';

        // Basic markdown-like formatting
        let html = this.escapeHtml(content);

        // Bold
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

        // Code inline
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Replace [SRC-N] citations with clickable badges
        html = html.replace(/\[SRC-(\d+)\]/g, (match, num) => {
            const key = `SRC-${num}`;
            const ref = refMap[key];
            if (ref) {
                const subject = this.escapeHtml(ref.subject || 'Email').substring(0, 30);
                const msgId = this.escapeHtml(ref.message_id || '');
                const sender = this.escapeHtml(ref.sender || '');
                const subjectFull = this.escapeHtml(ref.subject || '');
                const source = this.escapeHtml(ref.source || '');
                return `<span class="src-badge" title="${sender}: ${subjectFull}" data-message-id="${msgId}" data-subject="${subjectFull}" data-sender="${sender}" data-source="${source}" onclick="app.openEmail(this)">[${num}]</span>`;
            }
            return `<span class="src-badge src-badge-unknown">[${num}]</span>`;
        });

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        // Lists (simple)
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

        return html;
    }

    async openEmail(element) {
        const messageId = element.dataset.messageId || '';
        const subject = element.dataset.subject || '';
        const sender = element.dataset.sender || '';
        const source = element.dataset.source || '';

        if (!messageId && !subject) {
            return;
        }

        // Visual feedback
        element.style.opacity = '0.5';
        element.title = 'Opening...';

        try {
            const response = await fetch(`${this.apiBase}/api/email/open`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: messageId, subject, sender, source })
            });

            const result = await response.json();
            if (result.status === 'web' && result.url) {
                // IMAP email — open in browser
                window.open(result.url, '_blank');
                element.style.opacity = '1';
                element.title = 'Opened in browser';
            } else if (result.error) {
                element.title = `Could not open: ${result.error}`;
                element.style.opacity = '1';
                element.style.background = '#ff4444';
                setTimeout(() => { element.style.background = ''; }, 1500);
            } else {
                element.style.opacity = '1';
                element.title = 'Opened in Outlook';
            }
        } catch (error) {
            element.style.opacity = '1';
            element.title = 'Failed to open email';
            console.error('Failed to open email:', error);
        }
    }

    formatLocalTime(isoStr) {
        // Parse "2026-03-02T08:00:00" as local time, not UTC
        const [, timePart] = isoStr.split('T');
        if (!timePart) return '';
        const [h, m] = timePart.split(':');
        const hour = parseInt(h, 10);
        const minute = m || '00';
        const ampm = hour >= 12 ? 'PM' : 'AM';
        const h12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
        return `${h12}:${minute} ${ampm}`;
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

            this.animateCountUp('stat-total', stats.total_emails || 0);
            this.animateCountUp('stat-unread', stats.unread || 0);
            this.animateCountUp('stat-sent', stats.sent || 0);
            this.animateCountUp('stat-flagged', stats.flagged || 0);

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

            // Load analytics charts
            this.loadAnalytics();

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

    // Meetings
    async loadMeetings() {
        try {
            const response = await fetch(`${this.apiBase}/api/meetings?days=5`);
            const data = await response.json();

            if (data.error) {
                this.meetingsList.innerHTML = `<div class="empty-placeholder">${this.escapeHtml(data.error)}</div>`;
                return;
            }

            this.meetingsDate.textContent = data.start_date && data.end_date
                ? `${data.start_date} — ${data.end_date} (${data.meeting_count} meetings)`
                : '';

            if (!data.meetings || data.meetings.length === 0) {
                this.meetingsList.innerHTML = '<div class="empty-placeholder">No meetings scheduled in the next 5 business days</div>';
                return;
            }

            // Render grouped by date
            const byDate = data.by_date || {};
            const sortedDates = Object.keys(byDate).sort();

            let html = '';
            let globalIndex = 0;
            // Build a flat index map so prep button uses correct global index
            const indexMap = {};
            data.meetings.forEach((m, i) => { indexMap[JSON.stringify(m)] = i; });

            for (const dateKey of sortedDates) {
                const dayMeetings = byDate[dateKey];
                const dateObj = new Date(dateKey + 'T00:00:00');
                const dayLabel = dateObj.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });

                html += `<div class="meetings-day-header">${dayLabel}</div>`;

                for (const m of dayMeetings) {
                    // Find global index for this meeting in the flat meetings array
                    const idx = data.meetings.findIndex(dm =>
                        dm.subject === m.subject && dm.start === m.start);

                    // Outlook returns local times as naive ISO strings — parse without timezone conversion
                    const startTime = m.start ? this.formatLocalTime(m.start) : '';
                    const endTime = m.end ? this.formatLocalTime(m.end) : '';
                    const attendeeCount = (m.all_attendees || []).length;
                    const attendeeList = (m.all_attendees || []).slice(0, 4).map(a => this.escapeHtml(a)).join(', ');
                    const moreAttendees = attendeeCount > 4 ? ` +${attendeeCount - 4} more` : '';
                    const recurringBadge = m.is_recurring ? '<span class="recurring-badge">Recurring</span>' : '';

                    html += `
                        <div class="meeting-card">
                            <div class="meeting-time">
                                ${m.is_all_day ? '<span class="all-day-badge">All Day</span>' : `<span class="time-badge">${startTime} - ${endTime}</span>`}
                                ${m.duration_minutes ? `<span class="duration">${m.duration_minutes}min</span>` : ''}
                            </div>
                            <div class="meeting-details">
                                <div class="meeting-subject">${this.escapeHtml(m.subject || 'No Subject')} ${recurringBadge}</div>
                                ${m.location ? `<div class="meeting-location">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
                                        <circle cx="12" cy="10" r="3"/>
                                    </svg>
                                    ${this.escapeHtml(m.location)}
                                </div>` : ''}
                                ${m.organizer ? `<div class="meeting-organizer">Organizer: ${this.escapeHtml(m.organizer)}</div>` : ''}
                                ${attendeeCount > 0 ? `<div class="meeting-attendees">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                                        <circle cx="9" cy="7" r="4"/>
                                        <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                                        <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                                    </svg>
                                    ${attendeeList}${moreAttendees}
                                </div>` : ''}
                            </div>
                            <button class="btn-primary meeting-prep-btn" onclick="app.prepareMeeting(${idx}, '${this.escapeHtml(m.subject || 'Meeting').replace(/'/g, "\\'")}')">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                    <polyline points="14 2 14 8 20 8"/>
                                    <line x1="16" y1="13" x2="8" y2="13"/>
                                    <line x1="16" y1="17" x2="8" y2="17"/>
                                </svg>
                                Prepare
                            </button>
                        </div>
                    `;
                }
            }

            this.meetingsList.innerHTML = html;

        } catch (error) {
            console.error('Meetings load error:', error);
            this.meetingsList.innerHTML = '<div class="empty-placeholder">Error loading meetings</div>';
        }
    }

    async prepareMeeting(index, title) {
        // Show modal overlay
        const overlay = document.getElementById('meeting-prep-overlay');
        overlay.style.display = 'flex';
        this.prepMeetingTitle.textContent = `Prep: ${title}`;
        this.meetingPrepContent.innerHTML = '<div class="loading-placeholder">Generating prep brief...</div>';
        this.meetingPrepSources.innerHTML = '';

        try {
            const response = await fetch(`${this.apiBase}/api/meetings/${index}/prep?stream=true`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';
            let metadata = null;

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
                                this.meetingPrepContent.innerHTML = this.formatContent(fullContent);
                            } else if (parsed.type === 'metadata') {
                                metadata = parsed.content;
                            }
                        } catch (e) {
                            // Ignore parse errors
                        }
                    }
                }
            }

            // Show sources
            if (metadata && metadata.sources && metadata.sources.length > 0) {
                this.meetingPrepSources.innerHTML = `
                    <div class="prep-sources-header">Based on ${metadata.emails_found || 0} emails</div>
                    ${metadata.sources.map(s => `
                        <span class="source-tag">
                            ${this.escapeHtml(s.sender || 'Unknown')} — ${this.escapeHtml(s.subject || '')}
                        </span>
                    `).join('')}
                `;
            }

        } catch (error) {
            console.error('Meeting prep error:', error);
            this.meetingPrepContent.innerHTML = '<div class="empty-placeholder">Error generating meeting prep. Check that the LLM is running.</div>';
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

            // Populate backend dropdown from available backends
            const backends = settings.llm?.backends || [];
            if (backends.length > 0 && this.llmBackend) {
                this.llmBackend.innerHTML = backends.map(b =>
                    `<option value="${b.id}" ${!b.available ? 'disabled' : ''}>${b.name}${!b.available ? ' (no API key)' : ''}</option>`
                ).join('');
            }

            // Restore persisted backend selection
            this.llmBackend.value = this.backend;
            // If persisted value isn't available, fall back to local
            if (this.llmBackend.value !== this.backend) {
                this.backend = 'local';
                this.llmBackend.value = 'local';
                localStorage.setItem('inboxai-backend', 'local');
            }

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
        const backend = this.llmBackend.value;

        try {
            const response = await fetch(`${this.apiBase}/api/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model, temperature, backend })
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
    // Count-up animation
    animateCountUp(elementId, target) {
        const el = document.getElementById(elementId);
        if (!el) return;
        const duration = 600;
        const start = parseInt(el.textContent.replace(/,/g, '')) || 0;
        if (start === target) { el.textContent = target.toLocaleString(); return; }
        const startTime = performance.now();
        const step = (now) => {
            const progress = Math.min((now - startTime) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
            const current = Math.round(start + (target - start) * eased);
            el.textContent = current.toLocaleString();
            if (progress < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    }

    // Deep Research
    async startResearch() {
        const topic = this.researchTopic?.value.trim();
        if (!topic) return;

        // Show UI elements
        this.researchStats.style.display = '';
        this.researchSynthesis.style.display = '';
        this.researchTabs.style.display = '';
        this.researchSynthesisContent.innerHTML = '<div class="loading-placeholder">Researching...</div>';
        this.researchTimelineContainer.style.display = 'none';
        this.researchThreadsContainer.style.display = 'none';
        document.getElementById('research-email-count').textContent = '...';
        document.getElementById('research-thread-count').textContent = '...';

        // Switch to analysis tab
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === 'analysis'));
        document.getElementById('research-tab-analysis').style.display = '';
        document.getElementById('research-tab-topicmap').style.display = 'none';

        try {
            const response = await fetch(`${this.apiBase}/api/research`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic, stream: true, backend: this.backend })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';
            let refMap = {};
            let metadata = null;

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
                                this.researchSynthesisContent.innerHTML = this.formatContent(fullContent, refMap);
                            } else if (parsed.type === 'ref_map') {
                                refMap = parsed.content;
                            } else if (parsed.type === 'metadata') {
                                metadata = parsed.content;
                            }
                        } catch (e) { /* ignore */ }
                    }
                }
            }

            // Final render with ref_map
            this.researchSynthesisContent.innerHTML = this.formatContent(fullContent, refMap);

            // Render metadata
            if (metadata) {
                document.getElementById('research-email-count').textContent = metadata.total_emails || 0;
                document.getElementById('research-thread-count').textContent = metadata.total_threads || 0;

                if (metadata.timeline && metadata.timeline.length > 0) {
                    this.renderTimeline(metadata.timeline);
                    this.renderThreadList(metadata.timeline);
                }
            }

            // Auto-fetch topic map
            this.fetchTopicMap(topic);

        } catch (error) {
            console.error('Research error:', error);
            this.researchSynthesisContent.innerHTML = '<div class="empty-placeholder">Error performing research. Check that the LLM is running.</div>';
        }
    }

    renderTimeline(timeline) {
        this.researchTimelineContainer.style.display = '';
        let html = '';
        for (const item of timeline) {
            const dateStr = item.date_start ? new Date(item.date_start).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '';
            const participants = (item.participants || []).slice(0, 3).map(p => this.escapeHtml(p)).join(', ');
            html += `
                <div class="timeline-node">
                    <div class="timeline-date">${dateStr}</div>
                    <div class="timeline-subject">${this.escapeHtml(item.subject)}</div>
                    <div class="timeline-meta">
                        <span>${item.message_count} msg${item.message_count > 1 ? 's' : ''}</span>
                        <span>${participants}</span>
                        <span class="timeline-status ${item.status}">${item.status.replace('_', ' ')}</span>
                    </div>
                </div>
            `;
        }
        this.researchTimeline.innerHTML = html;
    }

    renderThreadList(timeline) {
        const threads = timeline.filter(t => t.type === 'thread');
        if (threads.length === 0) return;

        this.researchThreadsContainer.style.display = '';
        let html = '';
        for (const t of threads) {
            const dateStr = t.date_end ? new Date(t.date_end).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '';
            const participants = (t.participants || []).slice(0, 3).map(p => this.escapeHtml(p)).join(', ');
            html += `
                <div class="thread-card">
                    <div class="thread-card-subject">${this.escapeHtml(t.subject)}</div>
                    <div class="thread-card-meta">
                        <span>${t.message_count} messages</span>
                        <span>${participants}</span>
                        <span>${dateStr}</span>
                        <span class="timeline-status ${t.status}">${t.status.replace('_', ' ')}</span>
                    </div>
                </div>
            `;
        }
        this.researchThreads.innerHTML = html;
    }

    // Topic Map
    async fetchTopicMap(topic) {
        try {
            const response = await fetch(`${this.apiBase}/api/topic-map`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic })
            });
            const data = await response.json();
            this.renderTopicMap(data);
        } catch (error) {
            console.error('Topic map error:', error);
        }
    }

    renderTopicMap(data) {
        if (!data.nodes || data.nodes.length === 0) return;
        if (typeof vis === 'undefined') {
            console.warn('vis-network not loaded');
            return;
        }

        const container = document.getElementById('topic-map-container');
        container.innerHTML = '';

        const colorMap = {
            person: { background: '#8b5cf6', border: '#a78bfa', font: { color: '#fff' } },
            thread: { background: '#3b82f6', border: '#60a5fa', font: { color: '#fff' }, shape: 'box' },
            email: { background: '#22c55e', border: '#4ade80', font: { color: '#fff' } },
        };

        const visNodes = data.nodes.map(n => ({
            id: n.id,
            label: n.label,
            color: colorMap[n.type] || colorMap.email,
            shape: n.type === 'thread' ? 'box' : 'dot',
            size: n.type === 'person' ? 20 : 15,
            title: n.subject || n.email || n.label,
            font: { color: '#f4f4f6', size: 12 },
        }));

        const visEdges = data.edges.map(e => ({
            from: e.from,
            to: e.to,
            color: { color: 'rgba(139, 92, 246, 0.3)', highlight: '#8b5cf6' },
            arrows: 'to',
            smooth: { type: 'continuous' },
        }));

        const network = new vis.Network(container, {
            nodes: new vis.DataSet(visNodes),
            edges: new vis.DataSet(visEdges),
        }, {
            physics: {
                solver: 'forceAtlas2Based',
                forceAtlas2Based: { gravitationalConstant: -30, springLength: 100 },
                stabilization: { iterations: 100 },
            },
            nodes: {
                borderWidth: 2,
                shadow: true,
            },
            edges: {
                width: 1.5,
            },
            interaction: {
                hover: true,
                tooltipDelay: 100,
            },
        });

        // Click handler for node details
        const detailPanel = document.getElementById('topic-map-detail');
        network.on('click', (params) => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node = data.nodes.find(n => n.id === nodeId);
                if (node) {
                    detailPanel.style.display = '';
                    let html = `<strong>${this.escapeHtml(node.label)}</strong><br>`;
                    html += `<span style="color: var(--color-text-tertiary)">Type: ${node.type}</span><br>`;
                    if (node.subject) html += `Subject: ${this.escapeHtml(node.subject)}<br>`;
                    if (node.email) html += `Email: ${this.escapeHtml(node.email)}<br>`;
                    if (node.message_count) html += `Messages: ${node.message_count}<br>`;
                    if (node.date) html += `Date: ${new Date(node.date).toLocaleDateString()}<br>`;
                    detailPanel.innerHTML = html;
                }
            } else {
                detailPanel.style.display = 'none';
            }
        });
    }

    // ===== Entity Relationship Map =====

    async fetchEntityMap() {
        const subject = this.entityMapSubject?.value.trim();
        if (!subject) return;

        if (this.entityMapStats) this.entityMapStats.style.display = '';
        if (this.entityMapFilters) this.entityMapFilters.style.display = '';
        if (this.entityMapGraph) this.entityMapGraph.innerHTML = '<div class="loading-placeholder">Building entity map...</div>';
        if (this.entityMapDetail) this.entityMapDetail.style.display = 'none';
        if (this.entityMapTooltip) this.entityMapTooltip.style.display = 'none';
        const setEl = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        setEl('entity-people-count', '...');
        setEl('entity-topics-count', '...');
        setEl('entity-orgs-count', '...');
        setEl('entity-connections-count', '...');

        try {
            const response = await fetch(`${this.apiBase}/api/entity-map`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject })
            });
            const data = await response.json();

            if (data.error) {
                this.entityMapGraph.innerHTML = `<div class="empty-placeholder">${this.escapeHtml(data.error)}</div>`;
                return;
            }

            const stats = data.stats || {};
            setEl('entity-people-count', stats.people || 0);
            setEl('entity-topics-count', stats.topics || 0);
            setEl('entity-orgs-count', stats.organizations || 0);
            setEl('entity-connections-count', stats.connections || 0);

            this.entityMapData = data;
            this.renderEntityMap(data);
        } catch (error) {
            console.error('Entity map error:', error);
            this.entityMapGraph.innerHTML = '<div class="empty-placeholder">Error building entity map</div>';
        }
    }

    renderEntityMap(data) {
        if (!data.nodes || data.nodes.length === 0) {
            this.entityMapGraph.innerHTML = '<div class="empty-placeholder">No entities found for this subject</div>';
            return;
        }
        if (typeof vis === 'undefined') {
            console.warn('vis-network not loaded');
            return;
        }

        this.entityMapGraph.innerHTML = '';

        // Build a lookup map for raw node data (vis-network may strip custom props)
        this._entityNodeMap = new Map(data.nodes.map(n => [n.id, n]));

        // --- Tiered sizing ranges ---
        const TIER_SIZES = {
            person:       { A: [35, 50], B: [20, 35], C: [10, 20] },
            topic:        { A: [30, 42], B: [18, 30], C: [10, 18] },
            organization: { A: [38, 52], B: [24, 38], C: [14, 24] },
        };

        const sizeForNode = (n) => {
            const tier = n.tier || 'C';
            const typeKey = n.type || 'person';
            const range = (TIER_SIZES[typeKey] || TIER_SIZES.person)[tier] || [10, 20];
            const count = n.email_count || n.message_count || n.member_count || 1;
            const peers = data.nodes.filter(x => x.type === typeKey && x.tier === tier);
            const maxCount = Math.max(...peers.map(x => x.email_count || x.message_count || x.member_count || 1), 1);
            const t = maxCount > 1 ? (count - 1) / (maxCount - 1) : 0.5;
            return range[0] + t * (range[1] - range[0]);
        };

        // --- Build vis nodes ---
        const visNodes = data.nodes.map(n => {
            const size = sizeForNode(n);
            const fontSize = Math.max(10, Math.round(size * 0.35));

            if (n.type === 'organization') {
                return {
                    id: n.id, label: n.label, shape: 'hexagon', size,
                    color: { background: '#f59e0b', border: '#fbbf24',
                             highlight: { background: '#fbbf24', border: '#fde68a' } },
                    font: { color: '#f4f4f6', size: fontSize },
                };
            } else if (n.type === 'topic') {
                return {
                    id: n.id, label: n.label, shape: 'box', size,
                    color: { background: '#3b82f6', border: '#60a5fa',
                             highlight: { background: '#60a5fa', border: '#93bbfd' } },
                    font: { color: '#f4f4f6', size: fontSize, face: 'DM Sans' },
                    widthConstraint: { maximum: 200 },
                };
            } else {
                return {
                    id: n.id, label: n.label, shape: 'dot', size,
                    color: { background: '#8b5cf6', border: '#a78bfa',
                             highlight: { background: '#a78bfa', border: '#c4b5fd' } },
                    font: { color: '#f4f4f6', size: fontSize },
                };
            }
        });

        // --- Edge bundling: group edges by node pair, fan out multiples ---
        const pairEdges = {};
        data.edges.forEach(e => {
            const pairKey = [e.from, e.to].sort().join('||');
            if (!pairEdges[pairKey]) pairEdges[pairKey] = [];
            pairEdges[pairKey].push(e);
        });

        const visEdges = [];
        for (const [, edges] of Object.entries(pairEdges)) {
            const count = edges.length;
            edges.forEach((e, idx) => {
                const engagement = (e.reply_count || 0) + (e.quote_count || 0);
                let width;
                if (e.type === 'person_person') {
                    width = Math.min(1.5 + engagement * 0.5 + (e.weight || 0) * 0.3, 8);
                } else if (e.type === 'person_org') {
                    width = 1;
                } else {
                    width = Math.min(0.5 + (e.weight || 1) * 0.5, 4);
                }

                let roundness = 0;
                if (count > 1) {
                    const spread = 0.15;
                    roundness = -spread * (count - 1) / 2 + spread * idx;
                }

                const edgeObj = {
                    from: e.from, to: e.to, width,
                    smooth: count > 1
                        ? { type: 'curvedCW', roundness }
                        : { type: 'continuous' },
                };

                if (e.type === 'person_person') {
                    edgeObj.label = e.weight > 1 ? String(e.weight) : '';
                    edgeObj.color = { color: 'rgba(245,158,11,0.5)', highlight: '#f59e0b' };
                    edgeObj.font = { color: '#f59e0b', size: 10, strokeWidth: 0 };
                } else if (e.type === 'person_org') {
                    edgeObj.color = { color: 'rgba(245,158,11,0.2)', highlight: '#f59e0b' };
                    edgeObj.dashes = [4, 4];
                } else {
                    edgeObj.color = { color: 'rgba(139,92,246,0.2)', highlight: '#8b5cf6' };
                    edgeObj.arrows = 'to';
                }

                visEdges.push(edgeObj);
            });
        }

        // --- Create DataSets for filtering ---
        this.entityMapNodeDataSet = new vis.DataSet(visNodes);
        this.entityMapEdgeDataSet = new vis.DataSet(visEdges);

        // --- Network with barnesHut for collision detection ---
        const network = new vis.Network(this.entityMapGraph, {
            nodes: this.entityMapNodeDataSet,
            edges: this.entityMapEdgeDataSet,
        }, {
            physics: {
                solver: 'barnesHut',
                barnesHut: {
                    gravitationalConstant: -3000,
                    centralGravity: 0.1,
                    springLength: 220,
                    springConstant: 0.02,
                    damping: 0.15,
                    avoidOverlap: 0.8,
                },
                stabilization: { iterations: 200 },
            },
            nodes: {
                borderWidth: 2,
                shadow: { enabled: true, color: 'rgba(0,0,0,0.3)', size: 8 },
            },
            edges: { width: 1.5 },
            interaction: {
                hover: true,
                tooltipDelay: 0,
                hideEdgesOnDrag: true,
                multiselect: false,
            },
        });

        this.entityMapNetwork = network;

        // --- Hover: Fact Card tooltip ---
        network.on('hoverNode', (params) => {
            const raw = this._entityNodeMap.get(params.node);
            if (!raw) return;
            const canvasPos = params.pointer.DOM;
            this.showEntityTooltip(raw, canvasPos.x, canvasPos.y);
        });

        network.on('blurNode', () => {
            if (this.entityMapTooltip) this.entityMapTooltip.style.display = 'none';
        });

        // --- Click: detail sidebar ---
        network.on('click', (params) => {
            if (params.nodes.length > 0) {
                const raw = this._entityNodeMap.get(params.nodes[0]);
                if (raw) this.showEntityDetail(raw, data);
            } else {
                if (this.entityMapDetail) this.entityMapDetail.style.display = 'none';
            }
        });

        // --- Double-click to focus ---
        network.on('doubleClick', (params) => {
            if (params.nodes.length > 0) {
                network.focus(params.nodes[0], {
                    scale: 1.5,
                    animation: { duration: 500, easingFunction: 'easeInOutQuad' }
                });
            }
        });
    }

    showEntityTooltip(node, x, y) {
        const tip = this.entityMapTooltip;
        if (!tip) return;

        let html = '<div class="tooltip-header">';
        html += `<span class="tooltip-name">${this.escapeHtml(node.label)}</span>`;
        if (node.tier) html += `<span class="tooltip-tier tier-${node.tier.toLowerCase()}">Tier ${node.tier}</span>`;
        html += '</div>';

        if (node.type === 'person') {
            html += `<div class="tooltip-stat">${node.email_count} emails</div>`;
            if (node.email) html += `<div class="tooltip-stat">${this.escapeHtml(node.email)}</div>`;
            if (node.sentiments && node.sentiments.length > 0) {
                html += '<div style="margin-top:4px;">';
                node.sentiments.forEach(s => { html += `<span class="sentiment-tag ${s}">${s}</span>`; });
                html += '</div>';
            }
            if (node.action_items && node.action_items.length > 0) {
                html += '<div class="tooltip-actions"><strong style="font-size:0.7rem;color:var(--color-text-tertiary);text-transform:uppercase;">Action Items</strong>';
                html += '<ul style="margin:2px 0;padding-left:16px;">';
                node.action_items.slice(0, 3).forEach(item => {
                    html += `<li>${this.escapeHtml(item)}</li>`;
                });
                if (node.action_items.length > 3) {
                    html += `<li style="color:var(--color-text-tertiary);">+${node.action_items.length - 3} more...</li>`;
                }
                html += '</ul></div>';
            }
        } else if (node.type === 'organization') {
            html += `<div class="tooltip-stat">${node.member_count} members &bull; ${node.email_count} emails</div>`;
            html += `<div class="tooltip-stat">Domain: ${this.escapeHtml(node.domain)}</div>`;
        } else {
            html += `<div class="tooltip-stat">${node.message_count} messages</div>`;
            if (node.subject) html += `<div class="tooltip-stat" style="font-style:italic;">${this.escapeHtml(node.subject)}</div>`;
        }

        tip.innerHTML = html;
        tip.style.display = '';

        // Position near cursor within graph bounds
        const wrapper = tip.parentElement;
        const wrapperRect = wrapper.getBoundingClientRect();
        let left = x + 16;
        let top = y - 10;
        if (left + 340 > wrapperRect.width) left = x - 350;
        if (left < 0) left = 8;
        if (top + tip.offsetHeight > wrapperRect.height) top = wrapperRect.height - tip.offsetHeight - 8;
        if (top < 0) top = 8;
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
    }

    showEntityDetail(node, data) {
        if (!this.entityMapDetail) return;
        this.entityMapDetail.style.display = '';
        const content = this.entityMapDetailContent || this.entityMapDetail.querySelector('.detail-content');
        if (!content) return;

        let html = `<h3>${this.escapeHtml(node.label)}</h3>`;
        if (node.tier) html += `<span class="tooltip-tier tier-${node.tier.toLowerCase()}" style="margin-bottom:8px;display:inline-block;">Tier ${node.tier}</span> `;

        if (node.type === 'person') {
            html += `<p style="color:var(--color-text-tertiary);font-size:0.8125rem;">${this.escapeHtml(node.email)}</p>`;
            html += `<p style="color:var(--color-text-secondary);font-size:0.8125rem;">${node.email_count} emails</p>`;

            if (node.sentiments && node.sentiments.length > 0) {
                html += '<div style="margin:8px 0;">';
                node.sentiments.forEach(s => { html += `<span class="sentiment-tag ${s}">${s}</span>`; });
                html += '</div>';
            }
            if (node.action_items && node.action_items.length > 0) {
                html += '<h4>Action Items</h4><ul style="padding-left:16px;font-size:0.8125rem;color:var(--color-text-secondary);">';
                node.action_items.forEach(item => { html += `<li style="margin:4px 0;">${this.escapeHtml(item)}</li>`; });
                html += '</ul>';
            }

            const connectedTopics = data.edges
                .filter(e => e.type === 'person_topic' && e.from === node.id)
                .map(e => { const t = data.nodes.find(n => n.id === e.to); return t ? `${t.label} (${e.weight})` : null; })
                .filter(Boolean);
            if (connectedTopics.length > 0) {
                html += '<h4>Topics</h4>';
                connectedTopics.forEach(t => { html += `<div style="font-size:0.8125rem;color:var(--color-text-secondary);padding:2px 0;">&bull; ${this.escapeHtml(t)}</div>`; });
            }

            const connectedPeople = data.edges
                .filter(e => e.type === 'person_person' && (e.from === node.id || e.to === node.id))
                .map(e => { const oid = e.from === node.id ? e.to : e.from; const o = data.nodes.find(n => n.id === oid); return o ? `${o.label} (${e.weight} shared)` : null; })
                .filter(Boolean);
            if (connectedPeople.length > 0) {
                html += '<h4>Connected People</h4>';
                connectedPeople.forEach(p => { html += `<div style="font-size:0.8125rem;color:var(--color-text-secondary);padding:2px 0;">&bull; ${this.escapeHtml(p)}</div>`; });
            }
        } else if (node.type === 'organization') {
            html += `<p style="color:var(--color-text-tertiary);font-size:0.8125rem;">${this.escapeHtml(node.domain)}</p>`;
            html += `<p style="color:var(--color-text-secondary);font-size:0.8125rem;">${node.member_count} members, ${node.email_count} emails</p>`;
            if (node.members) {
                html += '<h4>Members</h4>';
                node.members.forEach(m => { html += `<div style="font-size:0.8125rem;color:var(--color-text-secondary);padding:2px 0;">&bull; ${this.escapeHtml(m)}</div>`; });
            }
        } else {
            html += `<p style="color:var(--color-text-secondary);font-size:0.8125rem;">${node.message_count} messages</p>`;
            const involved = data.edges
                .filter(e => e.type === 'person_topic' && e.to === node.id)
                .map(e => { const p = data.nodes.find(n => n.id === e.from); return p ? `${p.label} (${e.weight})` : null; })
                .filter(Boolean);
            if (involved.length > 0) {
                html += '<h4>People Involved</h4>';
                involved.forEach(p => { html += `<div style="font-size:0.8125rem;color:var(--color-text-secondary);padding:2px 0;">&bull; ${this.escapeHtml(p)}</div>`; });
            }
        }

        content.innerHTML = html;
    }

    applyEntityMapFilters() {
        if (!this.entityMapData || !this.entityMapNodeDataSet || !this.entityMapEdgeDataSet) return;

        const { type, sentiment } = this.activeFilters;
        const data = this.entityMapData;

        const visibleNodeIds = new Set();
        data.nodes.forEach(n => {
            let show = true;
            if (type === 'tier-a' && n.tier !== 'A') show = false;
            if (type === 'people' && n.type !== 'person') show = false;
            if (type === 'topics' && n.type !== 'topic') show = false;
            if (type === 'orgs' && n.type !== 'organization') show = false;

            if (sentiment !== 'all' && n.type === 'person') {
                const sentiments = n.sentiments || [];
                if (sentiment === 'urgent' && !sentiments.some(s => s === 'urgent' || s === 'risk')) show = false;
                if (sentiment === 'waiting' && !sentiments.includes('waiting')) show = false;
                if (sentiment === 'blocked' && !sentiments.includes('blocked')) show = false;
            }

            if (show) visibleNodeIds.add(n.id);
        });

        this.entityMapNodeDataSet.forEach(visNode => {
            this.entityMapNodeDataSet.update({ id: visNode.id, hidden: !visibleNodeIds.has(visNode.id) });
        });

        this.entityMapEdgeDataSet.forEach(visEdge => {
            const hidden = !visibleNodeIds.has(visEdge.from) || !visibleNodeIds.has(visEdge.to);
            this.entityMapEdgeDataSet.update({ id: visEdge.id, hidden });
        });
    }

    // Analytics Charts
    async loadAnalytics() {
        try {
            const response = await fetch(`${this.apiBase}/api/analytics`);
            const data = await response.json();

            // Chart.js dark theme defaults
            const textColor = '#a0a0aa';
            const gridColor = 'rgba(255, 255, 255, 0.06)';

            // Volume over time
            const dates = Object.keys(data.volume_by_date || {});
            if (dates.length > 0) {
                this.destroyChart('volume');
                const ctx = document.getElementById('volume-chart');
                if (ctx) {
                    this.charts.volume = new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: dates.map(d => {
                                const dt = new Date(d + 'T00:00:00');
                                return dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
                            }),
                            datasets: [
                                {
                                    label: 'Received',
                                    data: dates.map(d => data.volume_by_date[d].received),
                                    borderColor: '#8b5cf6',
                                    backgroundColor: 'rgba(139, 92, 246, 0.1)',
                                    fill: true,
                                    tension: 0.3,
                                },
                                {
                                    label: 'Sent',
                                    data: dates.map(d => data.volume_by_date[d].sent),
                                    borderColor: '#22c55e',
                                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                                    fill: true,
                                    tension: 0.3,
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            plugins: { legend: { labels: { color: textColor } } },
                            scales: {
                                x: { ticks: { color: textColor, maxTicksLimit: 10 }, grid: { color: gridColor } },
                                y: { ticks: { color: textColor }, grid: { color: gridColor } }
                            }
                        }
                    });
                }
            }

            // Top senders
            const senders = data.top_senders || [];
            if (senders.length > 0) {
                this.destroyChart('senders');
                const ctx = document.getElementById('senders-chart');
                if (ctx) {
                    this.charts.senders = new Chart(ctx, {
                        type: 'bar',
                        data: {
                            labels: senders.map(s => s.name.length > 20 ? s.name.substring(0, 20) + '...' : s.name),
                            datasets: [{
                                label: 'Emails',
                                data: senders.map(s => s.count),
                                backgroundColor: 'rgba(139, 92, 246, 0.6)',
                                borderColor: '#8b5cf6',
                                borderWidth: 1,
                            }]
                        },
                        options: {
                            indexAxis: 'y',
                            responsive: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: textColor }, grid: { color: gridColor } },
                                y: { ticks: { color: textColor, font: { size: 11 } }, grid: { color: gridColor } }
                            }
                        }
                    });
                }
            }

            // Hourly distribution
            const hourly = data.hourly_distribution || {};
            const hours = Object.keys(hourly).sort((a, b) => parseInt(a) - parseInt(b));
            if (hours.length > 0) {
                this.destroyChart('hourly');
                const ctx = document.getElementById('hourly-chart');
                if (ctx) {
                    this.charts.hourly = new Chart(ctx, {
                        type: 'bar',
                        data: {
                            labels: hours.map(h => `${h}:00`),
                            datasets: [{
                                label: 'Emails',
                                data: hours.map(h => hourly[h]),
                                backgroundColor: 'rgba(59, 130, 246, 0.6)',
                                borderColor: '#3b82f6',
                                borderWidth: 1,
                            }]
                        },
                        options: {
                            responsive: true,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { ticks: { color: textColor, maxTicksLimit: 12 }, grid: { color: gridColor } },
                                y: { ticks: { color: textColor }, grid: { color: gridColor } }
                            }
                        }
                    });
                }
            }

            // Sent vs received ratio
            const totalSent = dates.reduce((sum, d) => sum + (data.volume_by_date[d]?.sent || 0), 0);
            const totalReceived = dates.reduce((sum, d) => sum + (data.volume_by_date[d]?.received || 0), 0);
            if (totalSent > 0 || totalReceived > 0) {
                this.destroyChart('ratio');
                const ctx = document.getElementById('ratio-chart');
                if (ctx) {
                    this.charts.ratio = new Chart(ctx, {
                        type: 'doughnut',
                        data: {
                            labels: ['Received', 'Sent'],
                            datasets: [{
                                data: [totalReceived, totalSent],
                                backgroundColor: ['rgba(139, 92, 246, 0.7)', 'rgba(34, 197, 94, 0.7)'],
                                borderColor: ['#8b5cf6', '#22c55e'],
                                borderWidth: 2,
                            }]
                        },
                        options: {
                            responsive: true,
                            plugins: {
                                legend: { labels: { color: textColor }, position: 'bottom' }
                            }
                        }
                    });
                }
            }

        } catch (error) {
            console.error('Analytics load error:', error);
        }
    }

    destroyChart(name) {
        if (this.charts[name]) {
            this.charts[name].destroy();
            this.charts[name] = null;
        }
    }
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    window.app = new InboxAI();
});
