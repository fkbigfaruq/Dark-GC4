import time
import socket

BOT_NAME = "bot"


def handle_message(username, text):
    if not text:
        return None

    msg = text.strip().lower()

    # ----- simple commands -----
    if msg == "/about":
        return (
            "DARK_GC // underground cyber community\\n"
            "Anonymous global chat for coders, \\nhackers, builders and learners."
        )

        # =========================
    # COMMAND LIST
    # =========================
    elif msg == "/cmd":
        return (
            "AVAILABLE COMMANDS:\\n"
            "/about - info about DARK_GC\\n"
            "/ping - test bot response\\n"
            "/time - server time\\n"
            "/rules - group rules\\n"
            "/whoami - show your username\\n"
            "/ip <target> - lookup public ip\\n"
            "/clear - fake terminal clear"
        )

    elif msg == "!time":
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
        return (
            "DARK_GC RULES:\\n"
            "1. Respect members\\n"
            "2. No spam\\n"
            "3. No scams\\n"
            "4. Learn ethically\\n"
            "5. Build cool stuff"
        )
    # =========================
    # WHOAMI
    # =========================
    elif msg == "/whoami":
        return f"You are @{username}"

    # =========================
    # CLEAR
    # =========================
    elif msg == "/clear":
        return "\\n" * 25 + "terminal cleared"

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

    return None  # stay silent
