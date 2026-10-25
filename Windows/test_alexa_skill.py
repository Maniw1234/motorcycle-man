import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import datetime as dt
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import alexa_skill
import chat_link
import motorcycle_man as app
from test_chat_link import FakeHelper

SKILL = "amzn1.ask.skill.12345678-1234-1234-1234-123456789abc"
USER = "amzn1.ask.account.OWNER"


def alexa(kind="IntentRequest", intent="CommandIntent", command=None, skill=SKILL, user=USER, age=0):
    stamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    request = {"type": kind, "timestamp": stamp}
    if kind == "IntentRequest":
        request["intent"] = {"name": intent, "slots": {"command": {"name": "command", "value": command}} if command else {}}
    return {"version": "1.0", "context": {"System": {"application": {"applicationId": skill}, "user": {"userId": user}}},
            "request": request}


def said(answer):
    return answer["response"]["outputSpeech"]["text"]


class AlexaTests(unittest.TestCase):
    def setUp(self):
        self.helper = FakeHelper()
        self.link = chat_link.ChatLink("alexa", self.helper, slash=False)
        self.skill = alexa_skill.AlexaSkill(SKILL, self.link)

    def test_spoken_codes(self):
        self.assertEqual(alexa_skill.spoken_digits("four eight two 9 1 three"), "482913")
        self.assertEqual(alexa_skill.spoken_digits("link oh one two"), "012")

    def test_only_its_own_skill_and_fresh_requests(self):
        self.assertIsNone(self.skill.answer(alexa(skill="amzn1.ask.skill.someone-else")))
        self.assertIsNone(self.skill.answer(alexa(age=600)))  # a replayed old request
        self.assertIn("What should I do", said(self.skill.answer(alexa("LaunchRequest"))))
        self.assertEqual(said(self.skill.answer(alexa(intent="AMAZON.StopIntent"))), "Okay.")

    def test_linking_by_saying_the_code_then_commands(self):
        self.assertIn("isn't linked", said(self.skill.answer(alexa(command="open gmail"))))
        code = self.link.start_pairing()
        words = " ".join(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][int(d)] for d in code)
        self.assertIn("linked", said(self.skill.answer(alexa(command=f"link {words}"))))
        self.assertEqual((self.link.owner, self.helper.owners), (USER, [USER]))
        self.assertIn("isn't linked", said(self.skill.answer(alexa(command="open gmail", user="amzn1.ask.account.OTHER"))))
        self.assertEqual(said(self.skill.answer(alexa(command="open gmail"))), "Done: open gmail")
        self.assertEqual(self.helper.ran, ["open gmail"])
        with mock.patch.object(alexa_skill, "ANSWER_WITHIN", 0.05), \
                mock.patch.object(self.helper, "run", side_effect=lambda text: time.sleep(0.5) or "late"):
            self.assertIn("On it", said(self.skill.answer(alexa(command="research laptops"))))

    def test_the_secret_address_and_the_fixed_link_first(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(app.SetupNeeded):
            app._alexa_check({"skill_id": SKILL})
        app.save_setting("ngrok_domain", "bike-example.ngrok-free.app")
        app.save_key("ngrok_authtoken", "x" * 30)
        with self.assertRaises(app.SetupNeeded):
            app._alexa_check({"skill_id": "not a skill"})
        info = app._alexa_check({"skill_id": SKILL})
        self.assertGreaterEqual(len(info["path"]), 30)
        self.assertEqual(app._alexa_endpoint(info), f"https://bike-example.ngrok-free.app/alexa/{info['path']}")
        service = mock.Mock(transport=self.skill)
        service.info.return_value = info
        self.link.owner = USER
        with mock.patch.dict(app.CHAT_SERVICES, {"alexa": service}, clear=True):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 1000, 800))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            def post(path):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                connection.request("POST", path, json.dumps(alexa(command="pause the music")), {"Content-Type": "application/json"})
                reply = connection.getresponse()
                data = reply.read()
                connection.close()
                return reply.status, data

            self.assertEqual(post("/alexa/guessed")[0], 403)
            status, data = post(f"/alexa/{info['path']}")
            self.assertEqual((status, said(json.loads(data))), (200, "Done: pause the music"))
        voice_model = alexa_skill.VOICE_MODEL["interactionModel"]["languageModel"]
        self.assertEqual(voice_model["invocationName"], "motorcycle man")
        self.assertTrue(all("{command}" in s and s != "{command}" for s in voice_model["intents"][0]["samples"]))


if __name__ == "__main__":
    unittest.main()
