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
