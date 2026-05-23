/* DARK_GC enhancements: linkify URLs, @mention highlighting, image lightbox.
   Pure post-processing — does not touch existing send/emoji/socket logic. */
(function () {
  const URL_RE = /(https?:\/\/[^\s<>"']+)/g;
  const MENTION_RE = /(^|[\s(])@([A-Za-z0-9_]{2,32})/g;

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function transform(text) {
    let s = escapeHtml(text);
    s = s.replace(URL_RE, u =>
      `<a class="chat-link" href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`
    );
    s = s.replace(MENTION_RE, (_m, pre, name) =>
      `${pre}<span class="chat-mention">@${name}</span>`
    );
    return s;
  }

  function processBubble(bubble) {
    if (!bubble || bubble.dataset.enhanced === '1') return;
    bubble.dataset.enhanced = '1';
    const textNodes = [];
    bubble.childNodes.forEach(n => {
      if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim()) textNodes.push(n);
    });
    textNodes.forEach(n => {
      const span = document.createElement('span');
      span.innerHTML = transform(n.nodeValue);
      n.parentNode.replaceChild(span, n);
    });
  }

  function processAll(root) {
    (root || document).querySelectorAll('.bubble, .sys-bubble').forEach(processBubble);
  }

  // ---------- Image lightbox ----------
  function openLightbox(src) {
    const existing = document.querySelector('.lightbox');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.className = 'lightbox';
    overlay.innerHTML = `
      <img src="${src}" alt="">
      <div class="lightbox-actions">
        <a class="lightbox-btn" href="${src}" download target="_blank" rel="noopener">⬇ download</a>
        <button type="button" class="lightbox-btn lightbox-close">× close</button>
      </div>`;
    overlay.addEventListener('click', e => {
      if (e.target === overlay || e.target.classList.contains('lightbox-close')) {
        overlay.remove();
      }
    });
    document.addEventListener('keydown', function esc(ev) {
      if (ev.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', esc); }
    });
    document.body.appendChild(overlay);
  }

  document.addEventListener('click', e => {
    const t = e.target;
    if (t && t.tagName === 'IMG' && t.classList.contains('chat-img')) {
      e.preventDefault();
      openLightbox(t.src);
    }
  });

  // ---------- Initial + live processing ----------
  function init() {
    processAll();
    const messages = document.getElementById('messages');
    if (!messages || !('MutationObserver' in window)) return;
    const obs = new MutationObserver(muts => {
      muts.forEach(m => m.addedNodes.forEach(node => {
        if (node.nodeType !== 1) return;
        if (node.classList && (node.classList.contains('bubble') || node.classList.contains('sys-bubble'))) {
          processBubble(node);
        }
        processAll(node);
      }));
    });
    obs.observe(messages, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
