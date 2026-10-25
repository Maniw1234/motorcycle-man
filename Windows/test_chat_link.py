import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import chat_link
import motorcycle_man as app

OWNER, STRANGER = "owner-1", "stranger-9"


class FakeTransport:
    def __init__(self):
        self.sent, self.photos, self.typed = [], [], []

    def send(self, chat, text):
        self.sent.append((chat, text))

    def send_photo(self, chat, jpeg):
        self.photos.append((chat, jpeg))

    def typing(self, chat):
        self.typed.append(chat)

    def chat_for(self, owner):
        return f"dm-{owner}"


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

    def transcribe(self, audio, mime):
        self.heard.append((audio, mime))
        return "open notepad"

    def screenshot(self):
        return b"jpeg"

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


def wait_for(test, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.01)
    return False


class ChatLinkTests(unittest.TestCase):
    def setUp(self):
        self.helper, self.transport = FakeHelper(), FakeTransport()
        self.link = chat_link.ChatLink("test", self.helper)
        self.link.transport = self.transport

    def replies(self, chat):
        return [text for to, text in self.transport.sent if to == chat]

    def test_the_code_links_one_account_and_strangers_are_turned_away(self):
        code = self.link.start_pairing()
        wrong = "000000" if code != "000000" else "111111"
        self.link.receive("c-s", STRANGER, "open notepad")
        self.link.receive("c-s", STRANGER, wrong)
        self.assertEqual(self.replies("c-s"), ["This is a private Motorcycle Man.", "That's not the code Motorcycle Man shows on the PC."])
        self.link.receive("c-o", OWNER, code, name="Mani")
        self.assertEqual((self.link.owner, self.helper.owners, self.helper.states[-1]), (OWNER, [OWNER], ("paired", "Mani")))
        self.link.receive("c-s", STRANGER, code)
        self.link.receive("c-s", STRANGER, "open notepad")
        time.sleep(0.1)
        self.assertEqual((self.link.owner, self.helper.ran), (OWNER, []))

    def test_wrong_codes_run_out(self):
        code = self.link.start_pairing()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(chat_link.PAIR_TRIES):
            self.link.receive("c-s", STRANGER, wrong)
        self.link.receive("c-s", STRANGER, code)
        self.assertEqual(self.link.owner, "")

    def test_the_owner_s_commands_voice_files_and_screen(self):
        self.link.owner = OWNER
        self.link.receive("c", OWNER, "pause the music")
        self.assertTrue(wait_for(lambda: "Done: pause the music" in self.replies("c")))
        self.link.receive("c", OWNER, "", sound=(lambda: b"OggS", "audio/ogg"))
        self.assertTrue(wait_for(lambda: any("Done: open notepad" in r for r in self.replies("c"))))
        self.assertEqual(self.helper.heard, [(b"OggS", "audio/ogg")])
        self.link.receive("c", OWNER, "", files=[("notes.pdf", lambda: b"%PDF")])
        self.assertEqual(self.helper.saved, [("notes.pdf", b"%PDF")])
        self.link.receive("c", OWNER, "/screen")
        self.assertEqual(self.transport.photos, [("c", b"jpeg")])
        self.link.receive("c", OWNER, "stop")
        self.assertTrue(wait_for(lambda: "stop" in self.helper.ran))
        self.link.receive("c", OWNER, "help")
        self.assertIn("screen", self.replies("c")[-1])
        old = time.time() - 3600
        self.link.receive("c", OWNER, "delete the report", sent_at=old)
        self.assertIn("away", self.replies("c")[-1])

    def test_questions_are_answered_yes_or_no(self):
        self.link.owner = OWNER
        self.assertTrue(self.link.ask("live", "Send the email?"))
        self.assertEqual(self.transport.sent[-1][0], f"dm-{OWNER}")  # found the owner's chat by itself
        self.assertIn("Reply YES", self.transport.sent[-1][1])
        self.link.receive(f"dm-{OWNER}", OWNER, "yes")
        self.assertEqual(self.helper.answers, [("live", True)])
        self.assertEqual(self.replies(f"dm-{OWNER}")[-1], "Okay, allowed.")
        self.link.ask("ended", "Delete it?")
        self.link.asked_done("ended")  # answered on the PC
        self.link.receive(f"dm-{OWNER}", OWNER, "no")  # now just a word to Motorcycle Man
        self.assertTrue(wait_for(lambda: "no" in self.helper.ran))
        self.assertEqual(self.helper.answers, [("live", True)])
        self.assertTrue(self.link.notify("Reminder"))
        self.link.owner = ""
        self.assertFalse(self.link.notify("Reminder"))


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.helper, self.transport = FakeHelper(), FakeTransport()
        self.link = chat_link.ChatLink("test", self.helper, OWNER)
        self.link.transport = self.transport

    def replies(self, chat):
        return [text for to, text in self.transport.sent if to == chat]

    def test_a_guest_comes_in_with_the_owner_s_code_and_their_commands_run_with_the_owner_told(self):
        self.link.receive("c-o", OWNER, "add a guest")
        code = self.link.guest_code
        self.assertIn(code, self.replies("c-o")[-1])
        self.link.receive("c-g", "guest-5", code, name="Dana")
        self.assertEqual(self.link.guests, {"guest-5": "Dana"})
        self.assertEqual(self.helper.guest_lists[-1], {"guest-5": "Dana"})
        self.assertIn("Dana is now your guest", self.replies("c-o")[-1])
        self.assertTrue(self.replies("c-g")[-1].startswith("Hi Dana!"))
        self.link.receive("c-g", "guest-5", "pause the music")
        self.assertTrue(wait_for(lambda: "Done: pause the music" in self.replies("c-g")))
        self.assertEqual(self.helper.guest_runs, [("Dana", "pause the music")])
        self.assertIn("Dana: pause the music", self.replies("c-o")[-1])  # the owner sees what guests ask
        self.link.receive("c-g", "guest-5", "/screen")
        self.link.receive("c-g", "guest-5", "", files=[("x.exe", b"MZ")])
        self.assertEqual((self.transport.photos, self.helper.saved), ([], []))
        self.assertIn("Only the owner", self.replies("c-g")[-1])
        self.link.receive("c-x", "stranger", code)  # the code worked once only
        self.assertNotIn("stranger", self.link.guests)

    def test_a_guest_s_risky_command_is_asked_of_the_owner_not_the_guest(self):
        self.link.guests = {"guest-5": "Dana"}
        self.link.receive("c-o", OWNER, "help")  # the owner's chat is known
        self.assertTrue(self.link.ask("live", "Delete report.docx?"))
        self.assertIn("Delete report.docx?", self.replies("c-o")[-1])
        self.link.receive("c-g", "guest-5", "yes")  # a guest can't answer it
        time.sleep(0.1)
        self.assertEqual(self.helper.answers, [])
        self.link.receive("c-o", OWNER, "yes")
        self.assertEqual(self.helper.answers, [("live", True)])

    def test_listing_and_removing_guests(self):
        self.link.guests = {"g1": "Dana", "g2": "Sam"}
        self.link.receive("c-o", OWNER, "guests")
        self.assertEqual(self.replies("c-o")[-1], "Your guests: Dana, Sam")
        self.link.receive("c-o", OWNER, "remove guest dana")
        self.assertEqual(self.link.guests, {"g2": "Sam"})
        self.link.receive("c-o", OWNER, "remove guests")
        self.assertEqual((self.link.guests, self.helper.guest_lists[-1]), ({}, {}))
        self.link.receive("c-g", "g2", "open notepad")  # no longer a guest
        time.sleep(0.1)
        self.assertEqual(self.helper.ran, [])


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.started = []
        self.spec = {"kind": "fake", "label": "Fake", "about": "A fake app", "steps": "", "keys": [("token", "", True)],
                     "check": lambda values: {"name": "fakebot"},
                     "transport": lambda values, link: mock.Mock(start=lambda: self.started.append(values), stop=mock.Mock())}

    def test_keys_are_kept_and_a_new_key_forgets_the_old_owner(self):
        bridge = mock.Mock()
        service = app.ChatService(self.spec, bridge, 1000, 800)
        service.start()
        self.assertIsNone(service.transport)  # no key yet
        app.save_setting("fake_owner", "someone")
        code = service.use_keys({"token": "abc"}, {"name": "fakebot"})
        self.assertEqual((self.started, service.link.owner, service.link.code), ([{"token": "abc"}], "", code))
        self.assertEqual((app.saved_key("fake_token"), service.info()), ("abc", {"name": "fakebot"}))
        service.paired("me")
        service.start()
        self.assertEqual(service.link.owner, "me")  # remembered after a restart
        self.assertTrue(service.linked())
        service.forget()
        self.assertEqual((app.saved_key("fake_token"), service.link, service.transport), ("", None, None))
        bridge.chat_sig.emit.assert_called_with("fake", "off", "")

    def test_questions_and_alerts_go_to_the_chat_the_command_came_from_or_everywhere_when_away(self):
        here, other = mock.Mock(), mock.Mock()
        for link in (here.link, other.link):
            link.owner, link.asked = "x", {}
        with mock.patch.dict(app.CHAT_SERVICES, {"a": here, "b": other}, clear=True), \
                mock.patch.object(app, "user_is_away", return_value=False):
            app.CHAT_COMMAND.service = here
            try:
                app.chat_ask("q1", "Send it?")
            finally:
                app.CHAT_COMMAND.service = None
            self.assertTrue(wait_for(lambda: here.link.ask.called))
            time.sleep(0.05)
            other.link.ask.assert_not_called()
            self.assertFalse(app.chat_alert("Reminder"))
        with mock.patch.dict(app.CHAT_SERVICES, {"a": here, "b": other}, clear=True), \
                mock.patch.object(app, "user_is_away", return_value=True):
            app.chat_ask("q2", "Send it?")
            self.assertTrue(wait_for(lambda: other.link.ask.called))
            self.assertTrue(app.chat_alert("Reminder"))
            self.assertTrue(wait_for(lambda: here.link.notify.called and other.link.notify.called))


if __name__ == "__main__":
    unittest.main()
