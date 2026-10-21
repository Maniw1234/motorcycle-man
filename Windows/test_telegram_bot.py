import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import email.parser
import email.policy
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

import motorcycle_man as app
import telegram_bot

TOKEN = "123456789:test-bot-key-that-is-long-enough-xyz"
OWNER, STRANGER = 4242, 777


class FakeTelegram:
    # Telegram's Bot API on 127.0.0.1: it hands out the updates a test queues and records every call.
    def __init__(self):
        self.calls, self.updates, self.files = [], [], {}
        self.lock = threading.Lock()
        self.next_id = 100
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                prefix = f"/file/bot{TOKEN}/"
                data = fake.files.get(urllib.parse.unquote(self.path[len(prefix):])) if self.path.startswith(prefix) else None
                self.send_response(200 if data is not None else 404)
                self.send_header("Content-Length", str(len(data or b"")))
                self.end_headers()
                self.wfile.write(data or b"")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                token, _, method = self.path[len("/bot"):].partition("/")
                params, files = fake.read_body(self.headers.get("Content-Type", ""), body)
                if token != TOKEN:
                    return self.answer(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                with fake.lock:
                    fake.calls.append((method, params, files))
                result = True
                if method == "getMe":
                    result = {"id": 1, "is_bot": True, "username": "bike_test_bot"}
                elif method == "getUpdates":
                    with fake.lock:
                        result = [fake.updates.pop(0)] if fake.updates else []
                    if not result:
                        time.sleep(0.05)
                elif method == "sendMessage":
                    with fake.lock:
                        fake.next_id += 1
                        result = {"message_id": fake.next_id, "text": params.get("text")}
                elif method == "getFile":
                    result = {"file_id": params["file_id"], "file_path": params["file_id"],
                              "file_size": len(fake.files.get(params["file_id"], b""))}
                self.answer(200, {"ok": True, "result": result})

            def answer(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"

    @staticmethod
    def read_body(kind, body):
        if kind.startswith("multipart/"):
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + kind.encode() + b"\r\n\r\n" + body)
            params, files = {}, {}
            for part in message.iter_parts():
                if part.get_filename():
                    files[part.get_param("name", header="content-disposition")] = (part.get_filename(), part.get_payload(decode=True))
                else:
                    params[part.get_param("name", header="content-disposition")] = part.get_content().strip()
            return params, files
        return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}, {}

    def queue(self, *updates):
        with self.lock:
            for update in updates:
                self.next_id += 1
                self.updates.append(dict(update, update_id=self.next_id))

    def sent(self, method="sendMessage"):
        with self.lock:
            return [params for name, params, _ in self.calls if name == method]

    @staticmethod
    def wait_for(test, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.02)
        return False

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def message(sender, text="", **extra):
    return {"message": dict({"message_id": 1, "date": int(time.time()), "chat": {"id": sender, "type": "private"},
                             "from": {"id": sender}, "text": text}, **extra)}


class FakeHelper:
    def __init__(self):
        self.ran, self.heard, self.saved, self.answers, self.states, self.owners = [], [], [], [], [], []
        self.guest_runs, self.guest_lists = [], []

    def run(self, text, guest=None):
        self.ran.append(text)
        if guest:
            self.guest_runs.append((guest, text))
        return f"Done: {text}"

    def guests_changed(self, guests):
        self.guest_lists.append(guests)

    def transcribe(self, audio, mime_type):
        self.heard.append((audio, mime_type))
        return "open notepad"

    def screenshot(self):
        return b"\xff\xd8a picture\xff\xd9"

    def save(self, name, data):
        self.saved.append((name, data))
        return "C:\\Downloads\\Motorcycle Man\\" + name

    def answer(self, confirm_id, allowed):
        self.answers.append((confirm_id, allowed))
        return confirm_id == "live"

    def changed(self, state, detail):
        self.states.append((state, detail))

    def paired(self, owner):
        self.owners.append(owner)


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.telegram = FakeTelegram()
        self.addCleanup(self.telegram.close)
        for patcher in (mock.patch.object(telegram_bot, "API", self.telegram.address),
                        mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper = FakeHelper()

    def bot(self, owner=OWNER, token=TOKEN):
        bot = telegram_bot.TelegramBot(token, owner, self.helper)
        bot.start()
        self.addCleanup(bot.stop)
        self.assertTrue(self.telegram.wait_for(lambda: ("on", "bike_test_bot") in self.helper.states or self.helper.states))
        return bot

    def replies(self, chat):
        return [params["text"] for params in self.telegram.sent() if params["chat_id"] == str(chat)]

    def test_a_wrong_key_is_refused_without_showing_the_key(self):
        with self.assertRaises(telegram_bot.TelegramError) as refused:
            telegram_bot.call("1:wrong", "getMe")
        self.assertEqual(refused.exception.code, 401)
        self.assertNotIn("wrong", str(refused.exception))
        bot = self.bot(token="1:wrong")
        bot.thread.join(5)
        self.assertFalse(bot.thread.is_alive())  # it stops asking
        self.assertEqual(self.helper.states[-1][0], "bad key")

    def test_only_the_account_that_sends_the_code_is_let_in(self):
        bot = self.bot(owner=0)
        code = bot.start_pairing()
        wrong = "000000" if code != "000000" else "111111"
        self.telegram.queue(message(STRANGER, "open notepad"), message(STRANGER, f"/start {wrong}"))
        self.assertTrue(self.telegram.wait_for(lambda: len(self.replies(STRANGER)) == 2))
        self.assertEqual(self.replies(STRANGER), ["This is a private bot.", "That's not the code Motorcycle Man shows on the PC."])
        self.telegram.queue(message(OWNER, f"/start {code}"))
        self.assertTrue(self.telegram.wait_for(lambda: self.helper.owners == [OWNER]))
        self.assertEqual((bot.owner, bot.code), (OWNER, ""))
        self.assertTrue(self.telegram.wait_for(lambda: ("paired", "bike_test_bot") in self.helper.states))
        self.telegram.queue(message(STRANGER, f"/start {code}"), message(STRANGER, "open notepad"))
        self.assertTrue(self.telegram.wait_for(lambda: not self.telegram.updates))
        time.sleep(0.2)
        self.assertEqual(self.helper.ran, [])  # nothing a stranger says runs
        self.assertEqual(bot.owner, OWNER)  # and the code worked once only

    def test_wrong_codes_run_out(self):
        bot = self.bot(owner=0)
        code = bot.start_pairing()
        wrong = "000000" if code != "000000" else "111111"
        self.telegram.queue(*[message(STRANGER, wrong)] * telegram_bot.PAIR_TRIES, message(STRANGER, code))
        self.assertTrue(self.telegram.wait_for(lambda: not self.telegram.updates))
        time.sleep(0.2)
        self.assertEqual(bot.owner, 0)

    def test_the_owner_s_commands_voice_screen_and_files(self):
        self.bot()
        self.telegram.files = {"voice-1": b"OggS voice", "photo-big": b"\xff\xd8 photo", "doc-1": b"%PDF"}
        self.telegram.queue(message(OWNER, "pause the music"))
        self.assertTrue(self.telegram.wait_for(lambda: "Done: pause the music" in self.replies(OWNER)))
        self.assertTrue(self.telegram.wait_for(lambda: self.telegram.sent("sendChatAction")))  # "typing..." meanwhile
        self.telegram.queue(message(OWNER, "", voice={"file_id": "voice-1", "mime_type": "audio/ogg", "file_size": 10}))
        self.assertTrue(self.telegram.wait_for(lambda: any("Done: open notepad" in r for r in self.replies(OWNER))))
        self.assertEqual(self.helper.heard, [(b"OggS voice", "audio/ogg")])
        self.assertIn('"open notepad"', [r for r in self.replies(OWNER) if "Done: open notepad" in r][0])
        self.telegram.queue(message(OWNER, "/screen"))
        self.assertTrue(self.telegram.wait_for(lambda: self.telegram.sent("sendPhoto")))
        photo = [files for name, _, files in self.telegram.calls if name == "sendPhoto"][0]["photo"]
        self.assertEqual(photo, ("screen.jpg", b"\xff\xd8a picture\xff\xd9"))
        self.telegram.queue(message(OWNER, "", photo=[{"file_id": "photo-small", "file_size": 3}, {"file_id": "photo-big", "file_size": 7}]),
                            message(OWNER, "", document={"file_id": "doc-1", "file_name": "notes.pdf", "file_size": 4}))
        self.assertTrue(self.telegram.wait_for(lambda: len(self.helper.saved) == 2))
        self.assertEqual(self.helper.saved[0][1], b"\xff\xd8 photo")
        self.assertEqual(self.helper.saved[1], ("notes.pdf", b"%PDF"))
        self.assertTrue(self.telegram.wait_for(lambda: any("notes.pdf" in r for r in self.replies(OWNER))))
        self.telegram.queue(message(OWNER, "/stop"))
        self.assertTrue(self.telegram.wait_for(lambda: "stop" in self.helper.ran))

    def test_a_message_from_while_the_pc_was_off_is_not_run(self):
        self.bot()
        old = message(OWNER, "delete the report")
        old["message"]["date"] = int(time.time()) - 3600
        self.telegram.queue(old)
        self.assertTrue(self.telegram.wait_for(lambda: self.replies(OWNER)))
        self.assertIn("away", self.replies(OWNER)[0])
        self.assertEqual(self.helper.ran, [])

    def test_questions_come_with_allow_buttons(self):
        bot = self.bot()
        self.assertTrue(bot.ask("live", "Delete report.docx?"))
        sent = self.telegram.sent()[-1]
        buttons = json.loads(sent["reply_markup"])["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in buttons], ["ok:live", "no:live"])
        self.assertIn("Delete report.docx?", sent["text"])
        press = {"id": "q1", "data": "ok:live", "from": {"id": STRANGER},
                 "message": {"message_id": 5, "chat": {"id": OWNER}, "text": sent["text"]}}
        self.telegram.queue({"callback_query": press}, {"callback_query": dict(press, id="q2", **{"from": {"id": OWNER}})})
        self.assertTrue(self.telegram.wait_for(lambda: self.telegram.sent("editMessageText")))
        self.assertEqual(self.helper.answers, [("live", True)])  # the stranger's press did nothing
        self.assertIn("Allowed", self.telegram.sent("editMessageText")[0]["text"])
        self.assertNotIn("live", bot.asked)
        bot.ask("ended", "Send it?")
        bot.asked_done("ended")  # answered on the PC instead
        self.assertIn("(answered)", self.telegram.sent("editMessageText")[-1]["text"])

    def test_guests_on_telegram(self):
        bot = self.bot()
        self.telegram.queue(message(OWNER, "add a guest"))
        self.assertTrue(self.telegram.wait_for(lambda: bot.guest_code and any(bot.guest_code in r for r in self.replies(OWNER))))
        code = bot.guest_code
        guest_message = message(STRANGER, code)
        guest_message["message"]["from"]["first_name"] = "Dana"
        self.telegram.queue(guest_message)
        self.assertTrue(self.telegram.wait_for(lambda: bot.guests == {STRANGER: "Dana"}))
        self.telegram.queue(message(STRANGER, "pause the music"), message(STRANGER, "/screen"))
        self.assertTrue(self.telegram.wait_for(lambda: "Done: pause the music" in self.replies(STRANGER)))
        self.assertEqual(self.helper.guest_runs, [("Dana", "pause the music")])
        self.assertTrue(self.telegram.wait_for(lambda: any("Only the owner" in r for r in self.replies(STRANGER))))
        self.assertFalse(self.telegram.sent("sendPhoto"))
        self.assertTrue(any("Dana: pause the music" in r for r in self.replies(OWNER)))
        self.telegram.queue(message(OWNER, "remove guests"))
        self.assertTrue(self.telegram.wait_for(lambda: bot.guests == {} and self.helper.guest_lists[-1] == {}))

    def test_long_replies_are_split(self):
        bot = self.bot()
        bot.send(OWNER, "x" * (telegram_bot.MESSAGE_LIMIT + 10))
        self.assertEqual([len(r) for r in self.replies(OWNER)], [telegram_bot.MESSAGE_LIMIT, 10])


class TelegramInAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keys_are_kept_next_to_the_gemini_key(self):
        app.save_key("gemini_api_key", "AIza-gemini")
        app.save_key("telegram_bot_token", TOKEN)
        self.assertEqual((app.saved_key("telegram_bot_token"), app.saved_gemini_key()), (TOKEN, "AIza-gemini"))
        app.save_key("telegram_bot_token", "")
        self.assertEqual((app.saved_key("telegram_bot_token"), app.saved_gemini_key()), ("", "AIza-gemini"))
        self.assertTrue(app.TELEGRAM_KEY.fullmatch(app.clean_telegram_key(f"  {TOKEN.replace('test-', 'test_')}\n")))
        self.assertFalse(app.TELEGRAM_KEY.fullmatch("AIzaSyB-not-a-telegram-key"))

    def test_questions_go_to_telegram_when_the_user_is_away_or_asked_from_there(self):
        bot = mock.Mock(owner=OWNER, asked={})
        link = mock.Mock(bot=bot)
        with mock.patch.object(app, "TELEGRAM_LINK", link), mock.patch.object(app, "user_is_away", return_value=False):
            app.telegram_ask("a1", "Delete it?")
            time.sleep(0.1)
            bot.ask.assert_not_called()  # the user is at the PC or on the phone page
            app.TELEGRAM_COMMAND.active = True
            try:
                app.telegram_ask("a2", "Delete it?")
            finally:
                app.TELEGRAM_COMMAND.active = False
            self.assertTrue(FakeTelegram.wait_for(lambda: bot.ask.called))
            bot.ask.assert_called_once_with("a2", "Delete it?")
            self.assertFalse(app.telegram_alert("Reminder"))
        with mock.patch.object(app, "TELEGRAM_LINK", link), mock.patch.object(app, "user_is_away", return_value=True):
            self.assertTrue(app.telegram_alert("Reminder"))
            self.assertTrue(FakeTelegram.wait_for(lambda: bot.notify.called))
        bot.owner = 0
        with mock.patch.object(app, "TELEGRAM_LINK", link), mock.patch.object(app, "user_is_away", return_value=True):
            self.assertFalse(app.telegram_alert("Reminder"))  # not linked to anyone yet

    def test_a_question_asked_on_telegram_is_answered_through_the_app(self):
        bot = mock.Mock(owner=OWNER, asked={})
        bot.ask.side_effect = lambda request_id, question: threading.Timer(
            0.05, lambda: app.answer_confirmation(request_id, True)).start()
        bridge = mock.Mock()
        with mock.patch.object(app, "TELEGRAM_LINK", mock.Mock(bot=bot)), mock.patch.object(app, "user_is_away", return_value=True):
            self.assertTrue(app.ask_user_to_confirm(bridge, "Send the email?", timeout=5))

    def test_the_link_saves_files_and_needs_a_gemini_key_for_voice(self):
        link = app.TelegramLink(mock.Mock(), 1000, 800)
        with mock.patch.object(app, "phone_inbox", return_value=self.temp.name):
            where = link.save("notes.pdf", b"%PDF")
            again = link.save("notes.pdf", b"%PDF2")
        self.assertEqual(os.path.basename(where), "notes.pdf")
        self.assertEqual(os.path.basename(again), "notes (1).pdf")  # nothing is overwritten
        with open(again, "rb") as f:
            self.assertEqual(f.read(), b"%PDF2")
        with self.assertRaises(RuntimeError):
            link.transcribe(b"OggS", "audio/ogg")
        app.save_key("gemini_api_key", "AIza-gemini")
        with mock.patch.object(app, "transcribe_speech", return_value="open notepad") as transcribe:
            self.assertEqual(link.transcribe(b"OggS", "audio/ogg"), "open notepad")
        transcribe.assert_called_once_with(b"OggS", "AIza-gemini", "audio/ogg")

    def test_a_new_key_forgets_the_old_owner(self):
        link = app.TelegramLink(mock.Mock(), 1000, 800)
        app.save_setting("telegram_owner", OWNER)
        with mock.patch.object(telegram_bot.TelegramBot, "start"):
            code = link.use_key(TOKEN)
            self.assertEqual((link.bot.owner, link.bot.code, app.saved_key("telegram_bot_token")), (0, code, TOKEN))
            link.paired(OWNER)
            link.start()
            self.assertEqual(link.bot.owner, OWNER)  # remembered after a restart
            link.forget()
        self.assertIsNone(link.bot)
        self.assertEqual((app.saved_key("telegram_bot_token"), app.load_settings().get("telegram_owner")), ("", 0))


if __name__ == "__main__":
    unittest.main()
