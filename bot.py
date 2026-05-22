import time
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
