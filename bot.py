"""
DARK_GC bot — group helper + DM negotiation assistant.
- Group chat: simple !commands still work.
- DM admin negotiation: every locked room defaults to 500 unless BOT_ROOM_PRICES overrides it.
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
BOT_MIN_PERCENT = int(os.getenv("BOT_MIN_PERCENT", "100"))
BOT_CURRENCY = os.getenv("BOT_CURRENCY", "₦")
BOT_PAYMENT_INFO = os.getenv(
    "BOT_PAYMENT_INFO",
    "Please send payment proof to the admin here. The admin will approve your room access after confirmation. This is the Nira Acount details Number: 9137195754. Acount: Opay. Name: Umar Faruq Musa. and also in USD send via Phantom: resive_ID => F6MCggReRAN6rtrhJ2jRgHLVJtVknN3RAouPurHYSFwY"
)

try:
    BOT_ROOM_PRICES = json.loads(os.getenv("BOT_ROOM_PRICES", '{"default":500}'))
except Exception:
    BOT_ROOM_PRICES = {"default": 500}

PRICE_RE = re.compile(r"(?:\$|usd|ngn|₦|€)?\s*(\d{2,6})", re.I)
GREET_RE = re.compile(r"\b(hi|hello|hey|yo|good (morning|evening|afternoon))\b", re.I)
PRICE_Q = re.compile(r"\b(price|cost|how much|amount|fee|charge|pay)\b", re.I)
DEAL_Q = re.compile(r"\b(ok|okay|deal|agree|accept|i'll pay|i will pay|pay now|done)\b", re.I)
DECLINE_Q = re.compile(r"\b(too (high|much|expensive)|cheaper|discount|reduce|lower|last price)\b", re.I)


def _money(amount):
    return f"{BOT_CURRENCY}{amount}"


def _list_price(room_name):
    room_name = room_name or "default"
    for key, value in BOT_ROOM_PRICES.items():
        if key != "default" and key.lower() in room_name.lower():
            return int(value)
    return int(BOT_ROOM_PRICES.get("default", DEFAULT_ROOM_PRICE) or DEFAULT_ROOM_PRICE)


def _extract_offer(text):
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def handle_message(username, text):
    if not text:
        return None
    msg = text.strip().lower()
    if msg == "/hey":
        return f"hello {username} ho may i be of help."
    if msg == "/help":
        return "commands:/help, /time, /who"
    if msg == "/time":
        return "server time: " + time.strftime("%H:%M:%S")
    if msg == "/who":
        return f"you are: @{username}"
    if username != 'fkbigfaruq' and msg.startwith('https://'):
        return f"Sorry @{username} please you ae not allowed to send link here only admin's can"
    return None


def maybe_bot_reply(user_msg, room_name, admin_last_seen_ts, session_state):
    if not BOT_ENABLED:
        return None, session_state or {}
    if admin_last_seen_ts and ADMIN_IDLE_SECONDS > 0 and (time.time() - admin_last_seen_ts) < ADMIN_IDLE_SECONDS:
        return None, session_state or {}

    text = (user_msg or "").strip()
    if not text:
        return None, session_state or {}

    state = dict(session_state or {})
    list_price = _list_price(room_name)
    min_price = int(list_price * BOT_MIN_PERCENT / 100)
    if min_price <= 0:
        min_price = list_price

    if GREET_RE.search(text) and not state.get("greeted"):
        state["greeted"] = True
        state["last_quote"] = list_price
        return (
            f"Hey 👋 I'm {BOT_NAME}, the admin assistant. Access for {room_name or 'this room'} is the Nira Acount details Number: 9137195754. Acount: Opay. Name: Umar Faruq Musa. and also in USD send via Phantom: resive_ID => [ F6MCggReRAN6rtrhJ2jRgHLVJtVknN3RAouPurHYSFwY ]. Price in USD is 10$/20$"
            f"{_money(list_price)}. You can ask payment questions here.",
            state,
        )

    if DEAL_Q.search(text) and state.get("last_quote"):
        state["closed"] = True
        return (
            f"Good ✅ Price confirmed at {_money(state['last_quote'])}.\n\n{BOT_PAYMENT_INFO}",
            state,
        )

    offer = _extract_offer(text)
    if offer:
        if offer >= min_price:
            state["last_quote"] = offer
            return f"Deal 🤝 {_money(offer)} is acceptable. {BOT_PAYMENT_INFO}", state
        state["last_quote"] = min_price
        return f"I can't approve {_money(offer)}. The room price is {_money(list_price)}.", state

    if PRICE_Q.search(text):
        state["last_quote"] = list_price
        return f"Access to {room_name or 'any locked room'} is {_money(list_price)}.", state

    if DECLINE_Q.search(text):
        state["last_quote"] = min_price
        if min_price >= list_price:
            return f"The fixed room price is {_money(list_price)}.", state
        return f"Lowest I can approve is {_money(min_price)}. Want to continue?", state

    if not state.get("fallback_sent"):
        state["fallback_sent"] = True
        state["last_quote"] = list_price
        return f"Admin is not active right now right now. I can help with access. The room price is {_money(list_price)}. This is the Nira Acount details Number: 9137195754. Acount: Opay. Name: Umar Faruq Musa. and also in USD send via Phantom: resive_ID => [ F6MCggReRAN6rtrhJ2jRgHLVJtVknN3RAouPurHYSFwY ]. Price in USD is 10$/20$", state

    return None, state
