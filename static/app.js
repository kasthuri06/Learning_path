/* Theme: persistent preference */
(function () {
  var stored = localStorage.getItem('app-theme');
  var theme = (stored === 'light' || stored === 'dark') ? stored : 'dark';
  document.body.setAttribute('data-theme', theme);
  var btn = document.getElementById('theme-toggle');
  if (btn) btn.addEventListener('click', function () {
    var body = document.body;
    var next = body.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    body.setAttribute('data-theme', next);
    localStorage.setItem('app-theme', next);
    try { document.dispatchEvent(new CustomEvent('app-theme-changed', { detail: { theme: next } })); } catch (e) {}
  });
})();

/* Chatbot popup */
(function () {
  var toggle = document.getElementById('chatbot-toggle');
  var popup = document.getElementById('chatbot-popup');
  var closeBtn = document.getElementById('chatbot-close');
  var messagesEl = document.getElementById('chatbot-messages');
  var inputEl = document.getElementById('chatbot-input');
  var sendBtn = document.getElementById('chatbot-send');
  var modeBtns = document.querySelectorAll('.app-chatbot-popup [data-mode]');
  if (!popup || !toggle) return;
  var mode = 'general';
  var chatContext = {};
  function openChat() { popup.style.display = 'flex'; if (inputEl) inputEl.focus(); }
  function closeChat() { popup.style.display = 'none'; }
  toggle.addEventListener('click', openChat);
  if (closeBtn) closeBtn.addEventListener('click', closeChat);
  if (modeBtns.length) modeBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      mode = this.getAttribute('data-mode');
      modeBtns.forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');
      chatContext = {};
    });
  });
  function addMessage(text, isUser) {
    var div = document.createElement('div');
    div.className = 'app-chat-msg' + (isUser ? ' app-chat-msg--user' : '');
    if (!isUser && (text.indexOf('**') !== -1 || text.indexOf('\n') !== -1)) {
      div.innerHTML = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
    } else {
      div.textContent = text;
    }
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function sendMessage() {
    var text = (inputEl && inputEl.value || '').trim();
    if (!text) return;
    addMessage(text, true);
    if (inputEl) inputEl.value = '';
    fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text, mode: mode, context: chatContext }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        chatContext = d.context || {};
        addMessage(d.reply || 'No response.', false);
      })
      .catch(function () { addMessage('Failed to get response.', false); });
  }
  if (sendBtn) sendBtn.addEventListener('click', sendMessage);
  if (inputEl) inputEl.addEventListener('keydown', function (e) { if (e.key === 'Enter') sendMessage(); });
})();

/* ---- AI Personalization: Behavior Event Tracking ---- */
(function () {
  /**
   * recordBehaviorEvent — thin wrapper around POST /api/behavior/event
   * Silently fires and forgets; errors are logged but never thrown.
   */
  function recordBehaviorEvent(eventType, topic, roadmapId, payload) {
    if (!topic || !roadmapId) return;
    fetch('/api/behavior/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        topic: topic,
        roadmap_id: parseInt(roadmapId),
        payload: payload || {}
      })
    }).catch(function (e) { console.warn('recordBehaviorEvent failed:', e); });
  }

  // Hook: topic_view — fire when a roadmap-week card scrolls into view
  if ('IntersectionObserver' in window) {
    var viewedTopics = new Set();
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var card = entry.target;
        var topic = card.querySelector('.week-title')?.textContent.trim();
        var roadmapId = card.querySelector('[data-roadmap-id]')?.dataset.roadmapId;
        if (topic && roadmapId && !viewedTopics.has(topic)) {
          viewedTopics.add(topic);
          recordBehaviorEvent('topic_view', topic, roadmapId, {});
        }
      });
    }, { threshold: 0.4 });

    document.querySelectorAll('.roadmap-week').forEach(function (card) {
      observer.observe(card);
    });
  }

  // Hook: session_start / session_end — wire into existing timer buttons
  document.addEventListener('click', function (e) {
    var startBtn = e.target.closest('.timer-start-btn');
    var stopBtn = e.target.closest('.timer-stop-btn');

    if (startBtn) {
      var topic = startBtn.dataset.topic;
      var roadmapId = startBtn.dataset.roadmapId;
      recordBehaviorEvent('session_start', topic, roadmapId, { ts: Date.now() });
    }

    if (stopBtn) {
      var topic = stopBtn.dataset.topic;
      // timer-stop-btn doesn't always have roadmap-id; find it from the card
      var card = stopBtn.closest('.roadmap-week');
      var roadmapId = card?.querySelector('[data-roadmap-id]')?.dataset.roadmapId;
      recordBehaviorEvent('session_end', topic, roadmapId, { ts: Date.now() });
    }
  });

  // Hook: resource_complete — wire into existing resource-checkbox change
  document.addEventListener('change', function (e) {
    var cb = e.target.closest('.resource-checkbox');
    if (!cb || !cb.checked) return;
    var topic = cb.dataset.topic;
    var roadmapId = cb.dataset.roadmapId;
    var url = cb.dataset.url;
    recordBehaviorEvent('resource_complete', topic, roadmapId, { resource_url: url });
  });

  // Hook: note_added — fire when milestone notes textarea loses focus with content
  document.addEventListener('blur', function (e) {
    var ta = e.target.closest('.milestone-notes-input');
    if (!ta || !ta.value.trim()) return;
    var topic = ta.dataset.topic;
    var roadmapId = ta.dataset.roadmapId;
    recordBehaviorEvent('note_added', topic, roadmapId, {});
  }, true);

  // Expose globally for use in inline scripts if needed
  window.recordBehaviorEvent = recordBehaviorEvent;
})();
