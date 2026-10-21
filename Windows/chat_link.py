# A chat app's conversation with Motorcycle Man, whichever app carries it (Discord, email, WhatsApp, Instagram):
# who may use it, and what each message does. The account that sends the code Motorcycle Man shows becomes the
# owner; strangers are told it's private. The owner's messages are commands, voice messages are written down
# first, photos and files are saved on the PC, and when Motorcycle Man needs an OK the owner replies YES or NO.
# A transport (see discord_bot.py, email_link.py...) moves the messages and calls receive(); it offers
#   send(chat, text)                 required
#   send_photo(chat, jpeg)           optional, for "screen"
#   typing(chat)                     optional, shown while a command runs
#   chat_for(owner) -> chat          optional, where to write to the owner first (alerts)
# The helper does the work on the PC, as for the Telegram bot: run(text), transcribe(audio, mime), screenshot(),
# save(name, data), answer(confirm_id, allowed), changed(state, detail), paired(owner).
import logging, re, secrets, threading, time

LOG = logging.getLogger("motorcycle_man")
STALE_SECONDS = 120  # a message this old was sent while the PC was off, and is not acted on
PAIR_SECONDS = 15 * 60
PAIR_TRIES = 5
# Guests: the owner asks for a guest code and gives it to family or friends, who send it to the bot. A guest's
# commands run, but anything risky waits for the owner's OK (it is asked of the owner, never of the guest), they
# can't see the screen or send files, and the owner is told what each guest asks.
GUEST_ADD = re.compile(r"/?(?:add (?:a )?guest|invite(?: a)? guest|new guest|guest)", re.IGNORECASE)
GUEST_LIST = re.compile(r"/?(?:guests|list (?:my )?guests|who are my guests)\??", re.IGNORECASE)
GUEST_REMOVE = re.compile(r"/?(?:noguests|remove (?:all )?(?:my )?guests|remove guest (?P<name>.+))", re.IGNORECASE)


def guest_help() -> str:
    return ("You're a guest of Motorcycle Man. Send what to do on the PC: \"open YouTube\", \"pause the music\"... "
            "Anything risky waits for the owner's OK. \"stop\" stops what I'm doing.")


def guest_code_text(code: str) -> str:
    return (f"Give this guest code to your guest. They send it to me within 15 minutes: {code}\n\n"
            "Anything risky they ask for waits for your OK, they can't see the screen or send files, and I'll tell "
            "you what they ask. \"guests\" lists them, \"remove guests\" lets them all go.")


def guest_owner_command(text: str, guests: dict):
    # (what the owner asked about guests: "add", "list" or ("remove", name or None)), or None.
    said = " ".join(text.split())
    if GUEST_ADD.fullmatch(said):
        return "add"
    if GUEST_LIST.fullmatch(said):
        return "list"
    found = GUEST_REMOVE.fullmatch(said)
    if found:
        return "remove", (found.group("name") or "").strip() or None
    return None


def remove_guests(guests: dict, name):
    # The guests left, and the names that went.
    gone = [n for n in guests.values() if name is None or n.lower() == name.lower()]
    return {g: n for g, n in guests.items() if n not in gone}, gone


YES = re.compile(r"(?:yes|y|yeah|yep|ok|okay|allow|allow it|go ahead|do it|sure)[.! ]*", re.IGNORECASE)
NO = re.compile(r"(?:no|n|nope|don'?t|don'?t allow|stop|cancel|deny)[.! ]*", re.IGNORECASE)


def help_text(slash: bool = True) -> str:
    mark = "/" if slash else ""
    return ("Send me what you'd tell Motorcycle Man on your PC: \"open Gmail\", \"research the best laptops\", "
            "\"remind me at 6 pm to call Dana\"... A voice message works too.\n\n"
            f"{mark}screen  a picture of the PC's screen\n"
            f"{mark}stop  stop what I'm doing\n\n"
            "Photos and files you send me go to Downloads\\Motorcycle Man on the PC.")


class ChatLink:
    def __init__(self, kind: str, helper, owner: str = "", slash: bool = True, guests=None):
        self.kind, self.helper, self.owner, self.slash = kind, helper, str(owner or ""), slash
        self.guests = {str(g): str(n) for g, n in (guests or {}).items()}  # guest -> their name
        self.guest_code, self.guest_until = "", 0.0
        self.transport = None
        self.owner_chat = ""  # where the owner last wrote from
        self.code, self.code_until = "", 0.0
        self.tries, self.told = {}, {}
        self.waiting = {}  # chat -> the question waiting for its YES or NO
        self.asked = {}  # question -> chat

    def start_pairing(self) -> str:
        self.code, self.code_until, self.tries = f"{secrets.randbelow(10 ** 6):06d}", time.monotonic() + PAIR_SECONDS, {}
        return self.code

    # --- what comes in ---

    def receive(self, chat, sender, text: str = "", name: str = "", sent_at: float = None, sound=None, files=()):
        # sound: (bytes, or a function giving them, and the media type); files: [(name, bytes or a function)].
        sender, text = str(sender), str(text or "").strip()
        words = text.split()
        if sender in self.guests and sender != self.owner:
            return self._guest(chat, sender, text, sent_at, sound, files)
        if sender != self.owner or (self.code and words[-1:] == [self.code]):
            return self._stranger(chat, sender, words, name)
        self.owner_chat = chat
        if sent_at is not None and time.time() - sent_at > STALE_SECONDS:
            return self.say(chat, "I was away when you sent this. Send it again if you still want it.")
        if chat in self.waiting and (YES.fullmatch(text) or NO.fullmatch(text)):
            return self._answer(chat, bool(YES.fullmatch(text)))
        for file_name, data in files:  # kept first; any words with them are a command too
            self._keep(chat, file_name, data)
        about_guests = guest_owner_command(text, self.guests) if text else None
        if about_guests is not None:
            return self._manage_guests(chat, about_guests)
        word = words[0].lstrip("/").lower() if len(words) == 1 else ""
        if word in ("start", "help", "hi", "hello"):
            return self.say(chat, ("Hi! I'm Motorcycle Man. " if word != "help" else "") + help_text(self.slash))
        if word == "screen":
            if hasattr(self.transport, "send_photo"):
                return self._screen(chat)
            return self.say(chat, "Pictures of the screen don't come through here. Telegram, Discord, email or WhatsApp can.")
        if word == "stop":
            text = "stop"
        if text:
            self._work(chat, text)
        elif sound is not None:
            self._work(chat, "", sound)
        elif not files:
            self.say(chat, "I can take text, voice messages, photos and files.")

    def _stranger(self, chat, sender, words, name):
        sent = words[-1] if words else ""
        if self.code and time.monotonic() < self.code_until and self.tries.get(sender, 0) < PAIR_TRIES:
            if sent.isdigit() and secrets.compare_digest(sent, self.code):
                self.owner, self.code, self.owner_chat = sender, "", chat
                self.helper.paired(sender)
                self.helper.changed("paired", name)
                return self.say(chat, "You're linked! " + help_text(self.slash))
            if sent.isdigit():
                self.tries[sender] = self.tries.get(sender, 0) + 1
                return self.say(chat, "That's not the code Motorcycle Man shows on the PC.")
        if self.guest_code and time.monotonic() < self.guest_until and self.tries.get(sender, 0) < PAIR_TRIES:
            if sent.isdigit() and secrets.compare_digest(sent, self.guest_code):
                guest = " ".join(str(name or "").split())[:40] or "A guest"
                self.guests[sender], self.guest_code = guest, ""
                self.helper.guests_changed(dict(self.guests))
                self.notify(f"{guest} is now your guest on Motorcycle Man.")
                return self.say(chat, f"Hi {guest}! " + guest_help())
            if sent.isdigit():
                self.tries[sender] = self.tries.get(sender, 0) + 1
                return self.say(chat, "That's not the code.")
        if time.monotonic() - self.told.get(sender, -3600) >= 3600:
            self.told[sender] = time.monotonic()
            self.say(chat, "This is a private Motorcycle Man.")

    def _manage_guests(self, chat, asked):
        if asked == "add":
            self.guest_code, self.guest_until = f"{secrets.randbelow(10 ** 6):06d}", time.monotonic() + PAIR_SECONDS
            self.tries = {}
            return self.say(chat, guest_code_text(self.guest_code))
        if asked == "list":
            names = sorted(self.guests.values())
            return self.say(chat, ("Your guests: " + ", ".join(names)) if names else "You have no guests. Send \"add a guest\" for a code.")
        self.guests, gone = remove_guests(self.guests, asked[1])
        self.helper.guests_changed(dict(self.guests))
        self.say(chat, f"Okay, {', '.join(gone)} can't use Motorcycle Man any more." if gone else "No guest by that name.")

    def _guest(self, chat, sender, text, sent_at, sound, files):
        name = self.guests[sender]
        if sent_at is not None and time.time() - sent_at > STALE_SECONDS:
            return self.say(chat, "I was away when you sent this. Send it again if you still want it.")
        word = text.split()[0].lstrip("/").lower() if len(text.split()) == 1 else ""
        if files or word == "screen":
            return self.say(chat, "Only the owner can see the screen or send files.")
        if word in ("start", "help", "hi", "hello") or (not text and sound is None):
            return self.say(chat, guest_help())
        self.notify(f"\U0001F464 {name}: {text or '(a voice message)'}")
        self._work(chat, "stop" if word == "stop" else text, sound, guest=name)

    def _answer(self, chat, allowed: bool):
        confirm_id = self.waiting.pop(chat)
        self.asked.pop(confirm_id, None)
        taken = self.helper.answer(confirm_id, allowed)
        self.say(chat, ("Okay, allowed." if allowed else "Okay, I won't.") if taken else "Too late: that question has ended.")

    def _work(self, chat, text: str, sound=None, guest=None):
        # A command runs in its own thread, so the next messages (a stop, a YES) still come in.
        def go():
            done = threading.Event()
            if hasattr(self.transport, "typing"):
                def typing():
                    while True:
                        try:
                            self.transport.typing(chat)
                        except Exception:
                            pass
                        if done.wait(8):
                            return
                threading.Thread(target=typing, daemon=True).start()
            heard = reply = ""
            try:
                command = text
                if sound is not None:
                    audio, mime = sound
                    command = heard = self.helper.transcribe(audio() if callable(audio) else audio, mime)
                    if not command:
                        reply = "I couldn't make out any words. Try again?"
                if command:
                    reply = self.helper.run(command, guest=guest) if guest else self.helper.run(command)
            except Exception as exc:
                LOG.warning("A %s command failed: %s", self.kind, exc)
                reply = f"Error: {str(exc)[:300]}"
            finally:
                done.set()
            self.say(chat, (f"\U0001F399️ \"{heard}\"\n\n" if heard else "") + str(reply or "Done."))

        threading.Thread(target=go, daemon=True).start()

    def _keep(self, chat, file_name, data):
        try:
            where = self.helper.save(file_name, data() if callable(data) else data)
        except Exception as exc:
            return self.say(chat, f"I couldn't save {file_name}: {str(exc)[:200]}")
        self.say(chat, f"Saved on the PC: {where}")

    def _screen(self, chat):
        try:
            self.transport.send_photo(chat, self.helper.screenshot())
        except Exception as exc:
            self.say(chat, f"I couldn't send the screen: {str(exc)[:200]}")

    # --- what goes out ---

    def say(self, chat, text: str) -> bool:
        try:
            self.transport.send(chat, str(text) or "…")
            return True
        except Exception:
            LOG.warning("Could not answer on %s", self.kind, exc_info=True)
            return False

    def _owner_chat(self):
        if self.owner_chat:
            return self.owner_chat
        finder = getattr(self.transport, "chat_for", None)
        if finder is not None and self.owner:
            try:
                self.owner_chat = finder(self.owner) or ""
            except Exception:
                LOG.warning("Could not find the owner's chat on %s", self.kind, exc_info=True)
        return self.owner_chat

    def notify(self, text: str) -> bool:
        # A message to the owner, for alerts and reminders.
        chat = self._owner_chat() if self.owner and self.transport is not None else ""
        return bool(chat) and self.say(chat, text)

    def ask(self, confirm_id: str, question: str) -> bool:
        # Motorcycle Man needs the owner's OK: they reply YES or NO.
        chat = self._owner_chat() if self.owner and self.transport is not None else ""
        if not chat or not self.say(chat, f"⚠️ Motorcycle Man needs your OK:\n{question}\n\nReply YES to allow it, or NO."):
            return False
        self.waiting[chat], self.asked[confirm_id] = confirm_id, chat
        return True

    def asked_done(self, confirm_id: str):
        # The question was answered elsewhere, or nobody answered in time.
        chat = self.asked.pop(confirm_id, None)
        if chat is not None and self.waiting.get(chat) == confirm_id:
            del self.waiting[chat]
