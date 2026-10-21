# Motorcycle Man's Telegram bot: the user messages their PC from anywhere through Telegram, for free.
# The user makes a bot with Telegram's @BotFather and pastes its key into Motorcycle Man; the first chat that
# sends the code Motorcycle Man shows becomes the bot's owner, and only the owner is obeyed. Motorcycle Man
# asks Telegram for new messages (long polling), so nothing on the PC is opened to the internet.
# The bot's key is a secret: it is never logged or shown.
import json, logging, secrets, threading, time, urllib.error, urllib.parse, urllib.request, uuid

import chat_link

API = "https://api.telegram.org"
LOG = logging.getLogger("motorcycle_man")
POLL_SECONDS = 25
FILE_LIMIT = 20 << 20  # the Bot API hands bots files up to 20 MB
STALE_SECONDS = 120  # a message this old was sent while the PC was off, and is not acted on
MESSAGE_LIMIT = 4000  # characters a message (Telegram's own limit is 4096)
PAIR_SECONDS = 15 * 60  # how long a code to link a Telegram account works
PAIR_TRIES = 5  # wrong codes a Telegram account may send before it is ignored
HELP = ("Send me what you'd tell Motorcycle Man on your PC: \"open Gmail\", \"research the best laptops\", "
        "\"pause the music\", \"remind me at 6 pm to call Dana\"... A voice message works too.\n\n"
        "/screen  a picture of the PC's screen\n"
        "/stop  stop what I'm doing\n\n"
        "Photos and files you send me go to Downloads\\Motorcycle Man on the PC.")


class TelegramError(RuntimeError):
    def __init__(self, description, code=0):
        super().__init__(description)
        self.code = code


def call(token: str, method: str, params=None, files=None, timeout=POLL_SECONDS + 15):
    # One Bot API method; files maps a field to (file name, bytes, media type).
    fields = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in (params or {}).items() if v is not None}
    if files:
        boundary = uuid.uuid4().hex
        body = bytearray()
        for name, value in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, (file_name, data, kind) in files.items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{file_name}"\r\n'
                     f"Content-Type: {kind}\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        kind = f"multipart/form-data; boundary={boundary}"
    else:
        body, kind = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
    request = urllib.request.Request(f"{API}/bot{token}/{method}", data=bytes(body), headers={"Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read())
    except urllib.error.HTTPError as exc:  # its address holds the key, so it goes no further
        try:
            data = json.loads(exc.read())
        except ValueError:
            data = {}
        finally:
            exc.close()
        raise TelegramError(str(data.get("description") or f"Telegram answered {exc.code}"), exc.code) from None
    if not isinstance(data, dict) or not data.get("ok"):
        raise TelegramError(str(data.get("description", "Telegram refused")), int(data.get("error_code") or 0))
    return data.get("result")


def download(token: str, file_id: str, limit: int = FILE_LIMIT) -> bytes:
    info = call(token, "getFile", {"file_id": file_id}, timeout=30)
    if int(info.get("file_size") or 0) > limit:
        raise TelegramError("too big")
    address = f"{API}/file/bot{token}/{urllib.parse.quote(str(info.get('file_path', '')), safe='/')}"
    try:
        with urllib.request.urlopen(address, timeout=60) as reply:
            data = reply.read(limit + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise TelegramError(f"Telegram answered {exc.code}", exc.code) from None
    if len(data) > limit:
        raise TelegramError("too big")
    return data


def new_pair_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


class TelegramBot:
    # Reads the bot's messages in its own thread and answers the owner. helper does the work on the PC:
    #   run(text) -> reply                  a command, as if typed into Motorcycle Man
    #   transcribe(audio, mime) -> words    a voice message written down
    #   screenshot() -> JPEG bytes
    #   save(name, data) -> where it went   a photo or file the owner sent
    #   answer(confirm_id, allowed) -> bool an Allow / Don't allow button pressed
    #   changed(state, detail)              "on" (detail: the bot's name), "paired", "bad key", "failed"
    #   paired(owner_id)                    a Telegram account was linked with the code
    def __init__(self, token: str, owner: int, helper, guests=None):
        self.token, self.owner, self.helper = token, int(owner or 0), helper
        self.guests = {int(g): str(n) for g, n in (guests or {}).items()}  # guests (see chat_link): account -> name
        self.guest_code, self.guest_until, self.owner_chat = "", 0.0, 0
        self.name = ""
        self.code, self.code_until = "", 0.0
        self.tries = {}  # Telegram account -> wrong codes sent
        self.told = {}  # stranger -> when they were last told this bot is private
        self.asked = {}  # confirmation id -> (chat, message id, question)
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="telegram", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def start_pairing(self) -> str:
        self.code, self.code_until, self.tries = new_pair_code(), time.monotonic() + PAIR_SECONDS, {}
        return self.code

    def run(self):
        offset, delay, failed = None, 2, ""
        while not self.stopped.is_set():
            try:
                if not self.name:
                    self.name = str(call(self.token, "getMe", timeout=20).get("username", ""))
                    self.helper.changed("on", self.name)
                updates = call(self.token, "getUpdates", {"offset": offset, "timeout": POLL_SECONDS,
                                                          "allowed_updates": ["message", "callback_query"]})
                if failed:
                    self.helper.changed("on", self.name)  # back again
                delay, failed = 2, ""
            except TelegramError as exc:
                if exc.code in (401, 404):
                    self.helper.changed("bad key", "Telegram doesn't know this bot key. Set it up again.")
                    return
                reason = ("Another program is reading this bot's messages." if exc.code == 409
                          else f"Telegram: {str(exc)[:120]}")
                if reason != failed:
                    LOG.warning("Telegram bot: %s", reason)
                    self.helper.changed("failed", reason)
                    failed = reason
                self.stopped.wait(delay)
                delay = min(delay * 2, 60)
                continue
            except (OSError, ValueError):  # no internet for now
                if not failed:
                    self.helper.changed("failed", "Can't reach Telegram. Trying again...")
                    failed = "offline"
                self.stopped.wait(delay)
                delay = min(delay * 2, 60)
                continue
            if self.stopped.is_set():
                return
            for update in updates or []:
                offset = int(update.get("update_id", 0)) + 1
                try:
                    self.handle(update)
                except Exception:
                    LOG.exception("A Telegram message failed")

    # --- what comes in ---

    def handle(self, update: dict):
        if "callback_query" in update:
            return self.pressed(update["callback_query"])
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        sender = (message.get("from") or {}).get("id")
        if (message.get("chat") or {}).get("type") != "private" or not sender:
            return  # only one-to-one chats; a group the bot was added to is ignored
        text = str(message.get("text") or "").strip()
        if sender in self.guests and sender != self.owner:
            return self.guest(chat, sender, text, message)
        if sender != self.owner or (self.code and text.split()[-1:] == [self.code]):
            return self.stranger(chat, sender, text, message)
        self.owner_chat = chat
        if time.time() - float(message.get("date") or 0) > STALE_SECONDS:
            return self.send(chat, "I was away when you sent this. Send it again if you still want it.")
        about_guests = chat_link.guest_owner_command(text, self.guests) if text else None
        if about_guests is not None:
            return self.manage_guests(chat, about_guests)
        word = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if word in ("/start", "/help"):
            self.send(chat, ("Hi! I'm Motorcycle Man. " if word == "/start" else "") + HELP)
        elif word == "/screen":
            self.send_screen(chat)
        elif word == "/stop":
            self.work(chat, "stop")
        elif message.get("voice"):
            self.work(chat, "", message["voice"])
        elif text:
            self.work(chat, text)
        else:
            self.keep_file(chat, message)

    def stranger(self, chat, sender, text: str, message=None):
        # Only the account that sends the code Motorcycle Man shows is let in, and guests with a guest code.
        sent = text.split()[-1] if text else ""
        if self.guest_code and time.monotonic() < self.guest_until and self.tries.get(sender, 0) < PAIR_TRIES:
            if sent.isdigit() and secrets.compare_digest(sent, self.guest_code):
                person = (message or {}).get("from") or {}
                name = " ".join(f"{person.get('first_name', '')} {person.get('last_name', '')}".split())[:40] or "A guest"
                self.guests[sender], self.guest_code = name, ""
                self.helper.guests_changed(dict(self.guests))
                self.notify(f"{name} is now your guest on Motorcycle Man.")
                return self.send(chat, f"Hi {name}! " + chat_link.guest_help())
        if self.code and time.monotonic() < self.code_until and self.tries.get(sender, 0) < PAIR_TRIES:
            if sent.isdigit() and secrets.compare_digest(sent, self.code):
                self.owner, self.code = sender, ""
                self.helper.paired(sender)
                self.helper.changed("paired", self.name)
                return self.send(chat, "You're linked! 🏍️ " + HELP)
            if sent.isdigit():
                self.tries[sender] = self.tries.get(sender, 0) + 1
                return self.send(chat, "That's not the code Motorcycle Man shows on the PC.")
        if time.monotonic() - self.told.get(sender, -3600) >= 3600:
            self.told[sender] = time.monotonic()
            self.send(chat, "This is a private bot.")

    def manage_guests(self, chat, asked):
        if asked == "add":
            self.guest_code, self.guest_until, self.tries = new_pair_code(), time.monotonic() + PAIR_SECONDS, {}
            return self.send(chat, chat_link.guest_code_text(self.guest_code))
        if asked == "list":
            names = sorted(self.guests.values())
            return self.send(chat, ("Your guests: " + ", ".join(names)) if names else "You have no guests. Send \"add a guest\" for a code.")
        self.guests, gone = chat_link.remove_guests(self.guests, asked[1])
        self.helper.guests_changed(dict(self.guests))
        self.send(chat, f"Okay, {', '.join(gone)} can't use Motorcycle Man any more." if gone else "No guest by that name.")

    def guest(self, chat, sender, text: str, message: dict):
        name = self.guests[sender]
        if time.time() - float(message.get("date") or 0) > STALE_SECONDS:
            return self.send(chat, "I was away when you sent this. Send it again if you still want it.")
        word = text.split()[0].split("@")[0].lower().lstrip("/") if len(text.split()) == 1 else ""
        if word == "screen" or (not text and not message.get("voice")):
            return self.send(chat, "Only the owner can see the screen or send files.")
        if word in ("start", "help"):
            return self.send(chat, chat_link.guest_help())
        self.notify(f"\U0001F464 {name}: {text or '(a voice message)'}")
        self.work(chat, "stop" if word == "stop" else text, message.get("voice") if not text else None, guest=name)

    def pressed(self, query: dict):
        # Allow / Don't allow, under a question Motorcycle Man asked.
        kind, _, confirm_id = str(query.get("data", "")).partition(":")
        if (query.get("from") or {}).get("id") != self.owner or kind not in ("ok", "no"):
            return call(self.token, "answerCallbackQuery", {"callback_query_id": query.get("id")}, timeout=15)
        taken = self.helper.answer(confirm_id, kind == "ok")
        self.asked.pop(confirm_id, None)
        note = ("✅ Allowed" if kind == "ok" else "🚫 Not allowed") if taken else "⌛ Too late: that question has ended"
        call(self.token, "answerCallbackQuery", {"callback_query_id": query.get("id"), "text": note}, timeout=15)
        where = query.get("message") or {}
        if where.get("message_id"):
            self.edit(where["chat"]["id"], where["message_id"], f"{where.get('text', '')}\n\n{note}")

    def work(self, chat, text: str, sound=None, guest=None):
        # A command runs in its own thread, so the next messages (a /stop, an Allow) still come in.
        def go():
            done = threading.Event()

            def typing():  # "typing..." in Telegram until the reply comes, at least once
                while True:
                    try:
                        call(self.token, "sendChatAction", {"chat_id": chat, "action": "typing"}, timeout=10)
                    except (TelegramError, OSError):
                        pass
                    if done.wait(4.5):
                        return

            threading.Thread(target=typing, daemon=True).start()
            heard = reply = ""
            try:
                command = text
                if sound is not None:
                    command = heard = self.helper.transcribe(download(self.token, sound["file_id"]),
                                                             str(sound.get("mime_type") or "audio/ogg"))
                    if not command:
                        reply = "I couldn't make out any words. Try again?"
                if command:
                    reply = self.helper.run(command, guest=guest) if guest else self.helper.run(command)
            except Exception as exc:
                LOG.warning("A Telegram command failed: %s", exc)
                reply = f"Error: {str(exc)[:300]}"
            finally:
                done.set()
            self.send(chat, (f"🎙️ \"{heard}\"\n\n" if heard else "") + str(reply or "Done."))

        threading.Thread(target=go, daemon=True).start()

    def keep_file(self, chat, message: dict):
        stamp = time.strftime("%Y-%m-%d %H.%M.%S")
        if message.get("photo"):
            item, name = message["photo"][-1], f"Photo {stamp}.jpg"  # the biggest size
        else:
            item = message.get("document") or message.get("video") or message.get("audio") or message.get("video_note")
            if not item:
                return self.send(chat, "I can take text, voice messages, photos and files.")
            name = item.get("file_name") or f"File {stamp}{'.mp4' if 'video' in message or 'video_note' in message else ''}"
        if int(item.get("file_size") or 0) > FILE_LIMIT:
            return self.send(chat, "Telegram only gives bots files up to 20 MB. Send bigger ones from the phone page.")
        try:
            where = self.helper.save(name, download(self.token, item["file_id"]))
        except (TelegramError, OSError) as exc:
            return self.send(chat, f"I couldn't save that: {str(exc)[:200]}")
        self.send(chat, f"Saved on the PC: {where}")

    # --- what goes out ---

    def send(self, chat, text: str, buttons=None):
        text = str(text) or "…"
        last = None
        for start in range(0, len(text), MESSAGE_LIMIT):
            params = {"chat_id": chat, "text": text[start:start + MESSAGE_LIMIT], "link_preview_options": {"is_disabled": True}}
            if buttons and start + MESSAGE_LIMIT >= len(text):
                params["reply_markup"] = {"inline_keyboard": [buttons]}
            last = call(self.token, "sendMessage", params, timeout=20)
        return last

    def edit(self, chat, message_id, text: str):
        try:
            call(self.token, "editMessageText", {"chat_id": chat, "message_id": message_id, "text": text[:MESSAGE_LIMIT]}, timeout=15)
        except (TelegramError, OSError):
            pass

    def send_screen(self, chat):
        try:
            call(self.token, "sendChatAction", {"chat_id": chat, "action": "upload_photo"}, timeout=10)
            call(self.token, "sendPhoto", {"chat_id": chat}, {"photo": ("screen.jpg", self.helper.screenshot(), "image/jpeg")}, timeout=60)
        except (TelegramError, OSError) as exc:
            self.send(chat, f"I couldn't send the screen: {str(exc)[:200]}")

    def notify(self, text: str) -> bool:
        # A message to the owner, for alerts and reminders.
        if not self.owner:
            return False
        try:
            self.send(self.owner, text)
            return True
        except (TelegramError, OSError):
            LOG.warning("Could not send a Telegram alert", exc_info=True)
            return False

    def ask(self, confirm_id: str, question: str) -> bool:
        # Motorcycle Man needs the owner's OK: the question with Allow / Don't allow buttons.
        if not self.owner:
            return False
        try:
            sent = self.send(self.owner, f"⚠️ Motorcycle Man needs your OK:\n{question}",
                             [{"text": "✅ Allow", "callback_data": f"ok:{confirm_id}"},
                              {"text": "🚫 Don't allow", "callback_data": f"no:{confirm_id}"}])
        except (TelegramError, OSError):
            LOG.warning("Could not ask on Telegram", exc_info=True)
            return False
        self.asked[confirm_id] = (self.owner, (sent or {}).get("message_id"), question)
        return True

    def asked_done(self, confirm_id: str):
        # The question was answered elsewhere (on the PC or the phone page), or nobody answered in time.
        chat, message_id, question = self.asked.pop(confirm_id, (None, None, ""))
        if message_id:
            self.edit(chat, message_id, f"⚠️ Motorcycle Man needed your OK:\n{question}\n\n(answered)")
