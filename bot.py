
import socket

BOT_NAME = "bot"


def handle_message(username, text):
    if not text:
        return None

    msg = text.strip().lower()

    # ----- simple commands -----
    if msg == "/about":
        return """DARK_GC // underground 
        cyber community
        
        Anonymous global chat for coders,
        
        hackers, builders and learners."""

        # =========================
    # COMMAND LIST
    # =========================
    elif msg == "/cmd":
        return """ AVAILABLE COMMANDS:
        
        /about - info about DARK_GC
        
        /ping - test bot response
        
        /time - server time
        
        /rules - group rules
        
        /whoami - show your username
        
        /ip <target> - lookup public ip (e.g /ip google.com)"""

    elif msg == "/time":
        import time
        return f"server time: " + time.strftime("%H:%M:%S")

    # =========================
    # PING
    # =========================
    elif msg == "/ping":
        return "PONG // bot online"

    # ----- keyword replies -----
    elif "@bot" in msg:
        return f"yes how may i be of help to you @{username} 👋"

    elif "bye" in msg:
        return f"later @{username} 👻"

    # =========================
    # RULES
    # =========================
    elif msg == "/rules":
        return """DARK_GC RULES
        
        1. Respect members and have fun
        2. No spam
        3. No scams
        4. Learn ethically
        5. Build cool stuff"""
    # =========================
    # WHOAMI
    # =========================
    elif msg == "/whoami":
        return f"You are @{username}"

    # =========================
    # IP LOOKUP
    # =========================
    elif msg.startswith("/ip "):

        target = msg.replace("/ip ", "").strip()

        try:
            ip = socket.gethostbyname(target)

            return (
                f"TARGET: {target}\\n"
                f"IP: {ip}"
            )
        except:
            return "invalid domain or ip"

    return None  # stay silent

import os, re, json, time, random

BOT_ENABLED       = os.getenv("BOT_ENABLED", "1") == "1"
BOT_NAME          = os.getenv("BOT_NAME", "DarkBot")
ADMIN_IDLE_SECONDS= int(os.getenv("ADMIN_IDLE_SECONDS", "120"))
BOT_MIN_PERCENT   = int(os.getenv("BOT_MIN_PERCENT", "70"))
BOT_CURRENCY      = os.getenv("BOT_CURRENCY", "$")
BOT_PAYMENT_INFO  = os.getenv("BOT_PAYMENT_INFO",
    "Send payment to the admin wallet shown in your dashboard. "
    "Once received, your access is approved within minutes.")

try:
    BOT_ROOM_PRICES = json.loads(os.getenv("BOT_ROOM_PRICES", '{"Hacker":500,"Coding":500}'))
except Exception:
    BOT_ROOM_PRICES = {"Hacker": 500, "Coding": 500}

PRICE_RE  = re.compile(r"(?:\$|usd|ngn|₦|€)?\s*(\d{1,5})", re.I)
GREET_RE  = re.compile(r"\b(hi|hello|hey|yo|sup|good (morning|evening|afternoon))\b", re.I)
PRICE_Q   = re.compile(r"\b(price|cost|how much|amount|fee|charge)\b", re.I)
DEAL_Q    = re.compile(r"\b(ok|okay|deal|i agree|accept|i'll pay|pay now|sold)\b", re.I)
DECLINE_Q = re.compile(r"\b(too (high|much|expensive)|cheaper|discount|reduce|lower)\b", re.I)


def _list_price(room_name: str) -> int:
    if not room_name:
        return 0
    for k, v in BOT_ROOM_PRICES.items():
        if k.lower() in room_name.lower():
            return int(v)
    return int(BOT_ROOM_PRICES.get("default", 0) or 0)


def _extract_offer(text: str):
    m = PRICE_RE.search(text or "")
    if not m: return None
    try: return int(m.group(1))
    except: return None


def maybe_bot_reply(user_msg: str, room_name: str, admin_last_seen_ts: float, session_state: dict):
    """
    Returns (reply_text, new_session_state) or (None, session_state) if bot should stay quiet.
    session_state is a per-conversation dict you should persist (in db or memory).
    """
    if not BOT_ENABLED:
        return None, session_state
    # Admin recently active → stay silent, let admin handle it
    if admin_last_seen_ts and (time.time() - admin_last_seen_ts) < ADMIN_IDLE_SECONDS:
        return None, session_state

    text = (user_msg or "").strip()
    if not text:
        return None, session_state

    list_price = _list_price(room_name)
    min_price  = int(list_price * BOT_MIN_PERCENT / 100) if list_price else 0
    state = dict(session_state or {})

    # 1) Greeting
    if GREET_RE.search(text) and not state.get("greeted"):
        state["greeted"] = True
        return (f"Hey 👋 I'm {BOT_NAME}, the admin's assistant. "
                f"You're asking about *{room_name or 'a room'}*. "
                f"How can I help — pricing, access, or something else?"), state

    # 2) User accepts
    if DEAL_Q.search(text) and state.get("last_quote"):
        state["closed"] = True
        return (f"Awesome ✅ Locked at {BOT_CURRENCY}{state['last_quote']}.\n\n"
                f"{BOT_PAYMENT_INFO}\n\n"
                f"The admin will approve your access as soon as payment is confirmed."), state

    # 3) User made an offer
    offer = _extract_offer(text)
    if offer and list_price:
        if offer >= list_price:
            state["last_quote"] = offer
            return (f"Deal 🤝 {BOT_CURRENCY}{offer} works.\n{BOT_PAYMENT_INFO}"), state
        if offer >= min_price:
            counter = max(min_price, int((offer + list_price) / 2))
            state["last_quote"] = counter
            return (f"I can meet you partway at {BOT_CURRENCY}{counter} "
                    f"(list is {BOT_CURRENCY}{list_price}). Sound fair?"), state
        # below floor
        state["last_quote"] = min_price
        return (f"{BOT_CURRENCY}{offer} is below what I can authorize. "
                f"Lowest I can do is {BOT_CURRENCY}{min_price}. Wanna lock it in?"), state

    # 4) User asks about price
    if PRICE_Q.search(text) and list_price:
        state["last_quote"] = list_price
        return (f"Access to *{room_name}* is {BOT_CURRENCY}{list_price}. "
                f"I have a little room to negotiate if needed."), state

    # 5) Discount push
    if DECLINE_Q.search(text) and list_price:
        offer = random.randint(min_price, max(min_price, list_price - 5))
        state["last_quote"] = offer
        return (f"I hear you. Best I can do right now is {BOT_CURRENCY}{offer}. "
                f"Want to go ahead?"), state

    # 6) Fallback — only respond once per idle window so we don't spam
    if not state.get("fallback_sent"):
        state["fallback_sent"] = True
        return (f"The admin is offline right now, but I can help. "
                f"Ask me about pricing, payment, or room access for *{room_name or 'any room'}*."), state

    return None, state

