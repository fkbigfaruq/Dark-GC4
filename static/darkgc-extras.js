// DARK GC — notifications, connection status, toasts, PWA install
(function(){
  // ---- connection status pill ----
  const pill = document.createElement('div');
  pill.className = 'dgc-status online';
  pill.innerHTML = '<span class="dot"></span><span class="t">ONLINE</span>';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(pill));
  function setStatus(online){
    pill.classList.toggle('online', online);
    pill.classList.toggle('offline', !online);
    pill.querySelector('.t').textContent = online ? 'ONLINE' : 'OFFLINE';
    toast(online ? 'Back online ✅' : 'You are offline ⚠️');
  }
  window.addEventListener('online',  () => setStatus(true));
  window.addEventListener('offline', () => setStatus(false));

  // ---- toast helper ----
  const wrap = document.createElement('div');
  wrap.className = 'dgc-toast-wrap';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(wrap));
  function toast(text, ms=3000){
    const t = document.createElement('div');
    t.className = 'dgc-toast'; t.textContent = text;
    wrap.appendChild(t);
    setTimeout(() => { t.style.opacity = 0; setTimeout(()=>t.remove(), 300); }, ms);
  }
  window.dgcToast = toast;

  // ---- notifications ----
  async function ensureNotifPermission(){
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied')  return false;
    const p = await Notification.requestPermission();
    return p === 'granted';
  }
  async function notify(title, body, url){
    const ok = await ensureNotifPermission();
    if (!ok) return;
    if (document.visibilityState === 'visible') return; // user is here, skip OS notif
    if (navigator.serviceWorker?.controller){
      navigator.serviceWorker.controller.postMessage({ type:'notify', title, body, url, tag:'darkgc-msg' });
    } else {
      new Notification(title, { body, icon:'/static/icon-192.png' });
    }
  }
  window.dgcNotify = notify;

  // Hook socket.io if present
  document.addEventListener('DOMContentLoaded', () => {
    if (window.socket && typeof socket.on === 'function'){
      socket.on('message', (m) => {
        if (!m) return;
        const me = (window.CURRENT_USER || '').toLowerCase();
        if (m.username && m.username.toLowerCase() === me) return;
        const txt = m.text || m.message || '';
        notify(`💬 ${m.username || 'New message'}`, txt.slice(0,120), '/');
        if (document.visibilityState !== 'visible') toast(`${m.username}: ${txt.slice(0,60)}`);
      });
      socket.on('dm', (m) => notify(`✉️ DM from ${m.from}`, (m.text||'').slice(0,120), '/dm_inbox'));
      socket.on('connect',    () => setStatus(true));
      socket.on('disconnect', () => setStatus(false));
    }
  });

  // ---- enable-notifications button (only if not yet granted) ----
  document.addEventListener('DOMContentLoaded', () => {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') return;
    const b = document.createElement('button');
    b.className = 'dgc-enable-notif';
    b.textContent = '🔔 Enable notifications';
    b.onclick = async () => {
      const ok = await ensureNotifPermission();
      if (ok){ b.classList.add('hidden'); toast('Notifications enabled 🔔'); }
    };
    document.body.appendChild(b);
  });

  // ---- PWA: register service worker ----
  if ('serviceWorker' in navigator){
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
    });
  }

  // ---- mark admin bubbles (visual only) ----
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.msg').forEach(el => {
      const u = el.querySelector('.username');
      if (!u) return;
      const name = u.textContent.trim().toLowerCase();
      if ((window.ADMIN_USERS||[]).map(s=>s.toLowerCase()).includes(name)){
        el.classList.add('admin');
        u.classList.add('admin');
      }
    });
  });
})();
