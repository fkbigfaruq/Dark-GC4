// DARK_GC notifications, unread badges, connection status and PWA
(function () {
  if (window.__DARKGC_NOTIFICATIONS_LOADED__) return;
  window.__DARKGC_NOTIFICATIONS_LOADED__ = true;

  const APP_TITLE = 'Dark GHC website';
  const currentRoomId = () => Number(window.DARKGC_CURRENT_ROOM_ID || 0);
  const isVisible = () => document.visibilityState === 'visible';

  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  function toast(text, ms = 3200) {
    ready(() => {
      let wrap = document.querySelector('.dgc-toast-wrap');
      if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'dgc-toast-wrap';
        document.body.appendChild(wrap);
      }
      const item = document.createElement('div');
      item.className = 'dgc-toast';
      item.textContent = text;
      wrap.appendChild(item);
      setTimeout(() => {
        item.style.opacity = '0';
        setTimeout(() => item.remove(), 250);
      }, ms);
    });
  }
  window.dgcToast = toast;

  async function ensurePermission() {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    return (await Notification.requestPermission()) === 'granted';
  }

  // ---- Web Push subscription ----
  function urlBase64ToUint8Array(b64) {
    const padding = '='.repeat((4 - b64.length % 4) % 4);
    const base64 = (b64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  async function subscribeForPush() {
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
      const reg = await navigator.serviceWorker.ready;
      // already subscribed?
      const existing = await reg.pushManager.getSubscription();
      const res = await fetch('/api/push/vapid_public_key').then(r => r.json()).catch(() => null);
      if (!res || !res.enabled || !res.key) return;
      let sub = existing;
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(res.key),
        });
      }
      const body = JSON.stringify(sub.toJSON ? sub.toJSON() : sub);
      await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
    } catch (e) {
      console.warn('push subscribe failed', e);
    }
  }
  window.dgcSubscribePush = subscribeForPush;

  async function notify(title, body, url) {
    const ok = await ensurePermission();
    if (!ok || isVisible()) return;
    const payload = { type: 'notify', title, body, url: url || '/', tag: 'darkgc-message' };
    if (navigator.serviceWorker?.controller) {
      navigator.serviceWorker.controller.postMessage(payload);
    } else {
      new Notification(title, { body, icon: '/static/icon-192.png', badge: '/static/icon-192.png', data: { url: url || '/' } });
    }
  }
  window.dgcNotify = notify;

  function updateDot(selector, count) {
    const el = document.querySelector(selector);
    if (!el) return;
    const safe = Math.max(0, Number(count || 0));
    el.textContent = safe > 99 ? '99+' : String(safe);
    el.classList.toggle('hidden', safe <= 0);
  }

  function installStatus() {
    let pill = document.querySelector('.dgc-status');
    if (!pill) {
      pill = document.createElement('div');
      pill.className = 'dgc-status online';
      pill.innerHTML = '<span class="dot"></span><span class="t">ONLINE</span>';
      document.body.appendChild(pill);
    }
    const set = (online) => {
      pill.classList.toggle('online', online);
      pill.classList.toggle('offline', !online);
      pill.querySelector('.t').textContent = online ? 'ONLINE' : 'OFFLINE';
      toast(online ? 'Back online ✅' : 'Internet connection lost ⚠️');
    };
    window.addEventListener('online', () => set(true));
    window.addEventListener('offline', () => set(false));
    set(navigator.onLine);
  }

  function installButton() {
    if (!('Notification' in window) || Notification.permission === 'granted') return;
    const b = document.createElement('button');
    b.className = 'dgc-enable-notif';
    b.type = 'button';
    b.textContent = '🔔 Enable notifications';
    b.onclick = async () => {
      if (await ensurePermission()) {
        b.remove();
        toast('Notifications enabled 🔔');
        subscribeForPush();
      }
    };
    document.body.appendChild(b);
  }

  function wireSocket() {
    if (typeof io !== 'function') return;
    const sock = window.socket || io();
    if (!window.socket) window.socket = sock;

    sock.on('connect', () => toast('Connected ✅', 1200));
    sock.on('disconnect', () => toast('Connection interrupted ⚠️'));

    sock.on('room_unread', (m) => {
      if (!m) return;
      if (currentRoomId() && Number(m.room_id) === currentRoomId() && isVisible()) {
        fetch(`/api/rooms/${m.room_id}/read`, { method: 'POST' }).catch(() => {});
        updateDot(`#room-unread-${m.room_id}`, 0);
        return;
      }
      updateDot(`#room-unread-${m.room_id}`, m.count || 1);
      toast(`New message in ${m.room_name || 'room'}`);
      notify(APP_TITLE, `You have a message in ${m.room_name || 'a room'} from ${m.from || 'someone'}`, m.url || '/rooms');
    });

    sock.on('dm_unread', (m) => {
      if (!m) return;
      updateDot('#dm-unread', m.count || 1);
      toast(`New DM from ${m.from || 'admin'}`);
      notify(APP_TITLE, `You have a direct message from ${m.from || 'admin'}`, m.url || '/messages');
    });
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
      try {
        await navigator.serviceWorker.register('/static/sw.js');
        // if user already granted, make sure we have a push subscription on server
        if (Notification.permission === 'granted') {
          subscribeForPush();
        }
      } catch (e) { /* ignore */ }
    });
  }

  ready(() => {
    installStatus();
    installButton();
    wireSocket();
    document.addEventListener('visibilitychange', () => {
      if (isVisible() && currentRoomId()) fetch(`/api/rooms/${currentRoomId()}/read`, { method: 'POST' }).catch(() => {});
    });
  });
})();
