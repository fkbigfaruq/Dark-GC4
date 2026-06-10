"""
DARK_GC bot — v2.0
- Group chat: rich command set (/help, /rules, /price, /pay, /usd, /ngn,
  /time, /who, /joke, /quote, /roll, /coin, /uptime).
- Smart admin DM negotiation in both NGN and USD with
  ranges (USD 10–20, NGN configurable). Auto-detects currency from the user's
  message and counters with believable, human-sounding lines.
"""
import json
import os
import random
import re
import time

BOT_ENABLED = os.getenv("BOT_ENABLED", "1") == "1"
BOT_NAME = os.getenv("BOT_NAME", "DarkBot")
DEFAULT_ROOM_PRICE = int(os.getenv("BOT_DEFAULT_ROOM_PRICE", "500"))
ADMIN_IDLE_SECONDS = int(os.getenv("ADMIN_IDLE_SECONDS", "0"))
BOT_MIN_PERCENT = int(os.getenv("BOT_MIN_PERCENT", "50"))
BOT_CURRENCY = os.getenv("BOT_CURRENCY", "₦")

# USD range (user-facing; negotiable)
USD_MAX = int(os.getenv("BOT_USD_MAX", "20"))
USD_MIN = int(os.getenv("BOT_USD_MIN", "10"))

# Naira range
NGN_MAX = int(os.getenv("BOT_NGN_MAX", "20000"))
NGN_MIN = int(os.getenv("BOT_NGN_MIN", "8000"))

PAY_NGN = os.getenv(
    "BOT_PAY_NGN",
    "Opay → 9137195754 — Umar Faruq Musa"
)
PAY_USD = os.getenv(
    "BOT_PAY_USD",
    "Phantom (SOL) → F6MCggReRAN6rtrhJ2jRgHLVJtVknN3RAouPurHYSFwY"
)

BOT_START_TS = time.time()

try:
    BOT_ROOM_PRICES = json.loads(os.getenv("BOT_ROOM_PRICES", '{"default":500}'))
except Exception:
    BOT_ROOM_PRICES = {"default": 500}

USD_RE = re.compile(r"(?:\$|usd|dollar[s]?)\s*(\d{1,4})|(\d{1,4})\s*(?:\$|usd|dollar[s]?)", re.I)
NGN_RE = re.compile(r"(?:₦|ngn|naira|n)\s*(\d{3,7})|(\d{3,7})\s*(?:ngn|naira)", re.I)
ANY_NUM_RE = re.compile(r"\b(\d{2,7})\b")
GREET_RE = re.compile(r"\b(hi|hello|hey|yo|good\s*(morning|evening|afternoon)|sup|wagwan)\b", re.I)
PRICE_Q = re.compile(r"\b(price|cost|how\s*much|amount|fee|charge|pay|rate)\b", re.I)
DEAL_Q = re.compile(r"\b(ok|okay|deal|agree|accept|fine|i['’]?ll\s*pay|i\s*will\s*pay|pay\s*now|sending|sent|done)\b", re.I)
DECLINE_Q = re.compile(r"\b(too\s*(high|much|expensive)|cheaper|discount|reduce|lower|last\s*price|na\s*much|cost\s*too)\b", re.I)
USD_INTENT = re.compile(r"\b(usd|dollar|crypto|sol|phantom|btc)\b", re.I)
NGN_INTENT = re.compile(r"\b(ngn|naira|opay|bank|transfer|momo)\b", re.I)
LINK_RE = re.compile(r"https?://|www\.[a-z0-9]", re.I)

JOKES = [
    "Why do hackers prefer dark mode? Light attracts bugs.",
    "I tried to catch fog yesterday. Mist.",
    "There are 10 kinds of people: those who get binary and those who don't.",
]
QUOTES = [
    "“There is no patch for human stupidity.” — Kevin Mitnick",
    "“The quieter you become, the more you can hear.” — Kali tagline",
    "“Security is a process, not a product.” — Bruce Schneier",
]


def _list_price(room_name):
    room_name = room_name or "default"
    for key, value in BOT_ROOM_PRICES.items():
        if key != "default" and key.lower() in room_name.lower():
            return int(value)
    return int(BOT_ROOM_PRICES.get("default", DEFAULT_ROOM_PRICE) or DEFAULT_ROOM_PRICE)


def _money_ngn(n): return f"₦{n:,}"
def _money_usd(n): return f"${n}"


def _extract_amount(text):
    """Return (currency, amount) or (None, None). currency in {usd,ngn,unknown}."""
    m = USD_RE.search(text or "")
    if m:
        for g in m.groups():
            if g: return "usd", int(g)
    m = NGN_RE.search(text or "")
    if m:
        for g in m.groups():
            if g: return "ngn", int(g)
    m = ANY_NUM_RE.search(text or "")
    if m:
        n = int(m.group(1))
        # heuristic: small numbers = USD, big = NGN
        if n <= 100: return "usd", n
        if n >= 1000: return "ngn", n
    return None, None


def _payment_block(currency):
    if currency == "usd":
        return f"USD ➜ {PAY_USD}\nAfter paying, send the receipt screenshot here so the admin can approve."
    if currency == "ngn":
        return f"NGN ➜ {PAY_NGN}\nAfter paying, send the receipt screenshot here so the admin can approve."
    return f"NGN ➜ {PAY_NGN}\nUSD ➜ {PAY_USD}\nAfter paying, send the receipt screenshot here so the admin can approve."


def handle_message(username, text):
    """Group-chat command router. Returns reply text (string) or None."""
    if not text:
        return None
    msg = text.strip()
    low = msg.lower()

    if not low.startswith("/") and not low.startswith("!"):
        # passive link warning is handled server-side; nothing to do here
        return None

    cmd = low.lstrip("/!").split()[0] if low.lstrip("/!").strip() else ""
    if cmd in ("help", "commands"):
        return ("commands:\n"
                "/help  – this list\n"
                "/rules – room rules\n"
                "/price – room price\n"
                "/usd   – pay in dollars\n"
                "/ngn   – pay in naira\n"
                "/pay   – all payment options\n"
                "/time  – server time\n"
                "/who   – your username\n"
                "/uptime – bot uptime\n"
                "/joke  – random joke\n"
                "/quote – hacker quote\n"
                "/roll  – roll a d20\n"
                "/coin  – flip a coin")
    if cmd == "rules":
        return ("RULES:\n"
                "1. Only admins can send links.\n"
                "2. Wrap code in triple quotes:  \"\"\"...\"\"\"\n"
                "3. Be respectful. No spam. No doxxing.\n"
                "4. Pay via DM to admin — bot will help you.")
    if cmd == "price":
        return f"Locked-room access: {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)} USD or {_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)} NGN."
    if cmd == "usd":
        return f"USD: {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)}.\n{PAY_USD}"
    if cmd == "ngn":
        return f"NGN: {_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)}.\n{PAY_NGN}"
    if cmd == "pay":
        return _payment_block(None)
    if cmd == "time":
        return "server time: " + time.strftime("%H:%M:%S UTC", time.gmtime())
    if cmd == "who":
        return f"you are: @{username}"
    if cmd == "uptime":
        secs = int(time.time() - BOT_START_TS)
        h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
        return f"uptime: {h}h {m}m {s}s"
    if cmd == "joke":
        return random.choice(JOKES)
    if cmd == "quote":
        return random.choice(QUOTES)
    if cmd == "roll":
        return f"🎲 {random.randint(1, 20)}"
    if cmd == "coin":
        return "🪙 " + random.choice(["heads", "tails"])
    if cmd == "hey":
        return f"hello @{username}, how may I help."
    return None


def maybe_bot_reply(user_msg, room_name, admin_last_seen_ts, session_state):
    """DM negotiation. Smarter, with USD/NGN awareness."""
    if not BOT_ENABLED:
        return None, session_state or {}
    if admin_last_seen_ts and ADMIN_IDLE_SECONDS > 0 and (time.time() - admin_last_seen_ts) < ADMIN_IDLE_SECONDS:
        return None, session_state or {}

    text = (user_msg or "").strip()
    if not text:
        return None, session_state or {}

    state = dict(session_state or {})
    currency = state.get("currency")  # remember preference once set
    if USD_INTENT.search(text): currency = "usd"
    elif NGN_INTENT.search(text): currency = "ngn"

    cur_detected, offer = _extract_amount(text)
    if cur_detected and not currency:
        currency = cur_detected

    list_low  = USD_MIN if currency == "usd" else (NGN_MIN if currency == "ngn" else None)
    list_high = USD_MAX if currency == "usd" else (NGN_MAX if currency == "ngn" else None)
    money = _money_usd if currency == "usd" else (_money_ngn if currency == "ngn" else None)
    state["currency"] = currency

    # 1. Greeting
    if GREET_RE.search(text) and not state.get("greeted"):
        state["greeted"] = True
        return (
            f"Hey 👋 I'm {BOT_NAME} (admin's assistant — not a human). "
            f"Access for *{room_name or 'this room'}* is {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)} USD "
            f"or {_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)} NGN. Which works for you?",
            state,
        )

    # 2. Deal closed
    if DEAL_Q.search(text) and state.get("last_quote"):
        state["closed"] = True
        return (
            f"Sweet ✅ Price locked at {money(state['last_quote']) if money else state['last_quote']}.\n\n"
            f"{_payment_block(currency)}",
            state,
        )

    # 3. Specific offer
    if offer and currency:
        if offer >= list_low:
            offer = min(offer, list_high)
            state["last_quote"] = offer
            return (f"Deal 🤝 {money(offer)} works. {_payment_block(currency)}", state)
        # too low: counter
        counter = max(list_low, int((offer + list_low) / 2))
        state["last_quote"] = counter
        return (
            f"{money(offer)} is below the floor. Lowest I can do is {money(list_low)}. "
            f"Meet me at {money(counter)}?",
            state,
        )

    # 4. Asking the price
    if PRICE_Q.search(text):
        if currency == "usd":
            state["last_quote"] = USD_MAX
            return f"{room_name or 'This room'} is {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)} USD. {PAY_USD}", state
        if currency == "ngn":
            state["last_quote"] = NGN_MAX
            return f"{room_name or 'This room'} is {_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)} NGN. {PAY_NGN}", state
        return (f"{room_name or 'This room'}: {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)} USD "
                f"or {_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)} NGN. Which currency?"), state

    # 5. Pushback / discount request
    if DECLINE_Q.search(text):
        if currency == "usd":
            state["last_quote"] = USD_MIN
            return f"Lowest USD I can approve is {_money_usd(USD_MIN)}. Pay there and I'll forward proof to admin.", state
        if currency == "ngn":
            state["last_quote"] = NGN_MIN
            return f"Lowest NGN I can approve is {_money_ngn(NGN_MIN)}. Pay there and admin will approve.", state
        return ("Tell me your preferred currency (USD or NGN) and I'll give you my lowest."), state

    # 6. Fallback
    if not state.get("fallback_sent"):
        state["fallback_sent"] = True
        return (
            f"Admin isn't online right now. I can help you get access. "
            f"Range: {_money_usd(USD_MIN)}–{_money_usd(USD_MAX)} USD or "
            f"{_money_ngn(NGN_MIN)}–{_money_ngn(NGN_MAX)} NGN. "
            f"What works for you?",
            state,
        )

    return None, state
