"""
DARK_GC bot — beginner friendly.

How it works:
- Every chat message is passed to `handle_message(username, text)`.
- If you return a string, the bot replies with that string in the chat.
- If you return None, the bot stays quiet.

Add your own rules below using simple if-statements.
You don't need to touch app.py — just edit this file.
"""

BOT_NAME = "bot"


def handle_message(username, text):
    if not text:
        return None

    msg = text.strip().lower()

    # ----- simple commands -----
    if msg == "!ping":
        return f"pong 🏓"

    if msg == "!help":
        return f"commands: !ping, !help, !time, !who"

    if msg == "!time":
        import time
        return f"server time: " + time.strftime("%H:%M:%S")

    if msg == "!who":
        return f"you are: @{username}"

    # ----- keyword replies -----
    if "hello" in msg or "hi" in msg:
        return f"hey @{username} 👋"

    if "bye" in msg:
        return f"later @{username} 👻"

    # add more rules here:
    # if msg == "!something":
    #     return "your reply"

    return None  # stay silent
