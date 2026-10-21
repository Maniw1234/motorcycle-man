import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from PySide6.QtWidgets import QApplication

import ai_providers
import motorcycle_man as app
from test_motorcycle_man import ANSWER_TEXT

QT = QApplication.instance() or QApplication([])
PICTURE = {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}}
PLAN_REQUEST = {"contents": [{"parts": [PICTURE, {"text": "Open Gmail"}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}


def reply(data):
    answer = io.BytesIO(json.dumps(data).encode("utf-8"))
    answer.__enter__ = lambda self=answer: self
    answer.__exit__ = lambda self, *a: None
    return answer


class FakeAnswer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def answered(data):
    return lambda request, timeout=None: FakeAnswer(json.dumps(data).encode("utf-8"))


class FakeClaude:
    # Stands in for anthropic.Anthropic: remembers what was asked and answers with text.
    def __init__(self, text='{"actions": []}', stop="end_turn", models=("claude-opus-5-5", "claude-haiku-5-5")):
        self.asked, self.text, self.stop, self.model_ids = [], text, stop, models
        message = lambda **request: (self.asked.append(request), SimpleNamespace(
            stop_reason=self.stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=self.text)]))[1]
        self.messages = SimpleNamespace(create=message)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=message))
        self.models = SimpleNamespace(list=lambda limit=100: [SimpleNamespace(id=m) for m in self.model_ids])

    def __call__(self, **settings):
        self.settings = settings
        return self


class TranslationTests(unittest.TestCase):
    def test_a_gemini_request_in_plain_parts(self):
        system, items = ai_providers.plain_parts(dict(PLAN_REQUEST, system_instruction={"parts": [{"text": "Be brief."}]}))
        self.assertEqual(system, "Be brief.")
        self.assertEqual(items, [("image", "image/jpeg", "QUJD"), ("text", "Open Gmail")])
        self.assertTrue(ai_providers.wants_json(PLAN_REQUEST))

    def test_claude_gets_the_picture_and_answers_like_gemini(self):
        claude = FakeClaude()
        data = ai_providers.ask_claude("sk-ant-test", "claude-opus-5-5", PLAN_REQUEST, 45, client_factory=claude)
        self.assertEqual(data["candidates"][0]["content"]["parts"][0]["text"], '{"actions": []}')
        asked = claude.asked[0]
        self.assertEqual(asked["messages"][0]["content"][0],
                         {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}})
        self.assertIn("only the JSON object", asked["system"])
        self.assertEqual(asked["output_config"], {"effort": "low"})
        self.assertEqual((asked["betas"], asked["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))
        self.assertNotIn("temperature", asked)  # current Claude models turn it down
        self.assertEqual(claude.settings["api_key"], "sk-ant-test")

    def test_older_claude_models_get_a_plain_request(self):
        claude = FakeClaude()
        ai_providers.ask_claude("sk-ant-test", "claude-haiku-4-5", PLAN_REQUEST, 45, client_factory=claude)
        self.assertNotIn("output_config", claude.asked[0])
        self.assertNotIn("fallbacks", claude.asked[0])

    def test_claude_declining_is_said(self):
        with self.assertRaises(ai_providers.AiError) as said:
            ai_providers.ask_claude("k", "claude-opus-5-5", PLAN_REQUEST, 45, client_factory=FakeClaude(stop="refusal"))
        self.assertIn("declined", str(said.exception))
        with self.assertRaises(ai_providers.AiError):  # Claude can't listen
            ai_providers.ask_claude("k", "claude-opus-5-5", {"contents": [{"parts": [
                {"inline_data": {"mime_type": "audio/wav", "data": "AA=="}}]}]}, 45, client_factory=FakeClaude())

    def test_chatgpt_and_compatible_services(self):
        asked = []

        def opener(request, timeout=None):
            asked.append(request)
            return FakeAnswer(json.dumps({"choices": [{"message": {"content": '{"actions": []}'}, "finish_reason": "stop"}]}).encode())
        data = ai_providers.ask_openai("openai", "sk-test", "gpt-5-mini", "", PLAN_REQUEST, 45, opener)
        self.assertEqual(data["candidates"][0]["content"]["parts"][0]["text"], '{"actions": []}')
        body = json.loads(asked[0].data)
        self.assertEqual(asked[0].full_url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(asked[0].get_header("Authorization"), "Bearer sk-test")
        self.assertEqual(body["messages"][1]["content"][0], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}})
        self.assertEqual((body["response_format"], body["reasoning_effort"]), ({"type": "json_object"}, "low"))
        self.assertIn("max_completion_tokens", body)
        ai_providers.ask_openai("compatible", "", "llama3.2-vision", "http://localhost:11434/v1/", PLAN_REQUEST, 45, opener)
        body = json.loads(asked[1].data)
        self.assertEqual(asked[1].full_url, "http://localhost:11434/v1/chat/completions")
        self.assertIsNone(asked[1].get_header("Authorization"))  # Ollama needs no key
        self.assertNotIn("response_format", body)
        self.assertEqual(body["max_tokens"], 8192)

    def test_errors_in_plain_words(self):
        def refused(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b'{"error": {"message": "bad key"}}'))
        with self.assertRaises(ai_providers.AiError) as said:
            ai_providers.ask_openai("openai", "sk-wrong", "gpt-5-mini", "", PLAN_REQUEST, 45, refused)
        self.assertEqual((str(said.exception), said.exception.status), ("ChatGPT didn't accept the key.", 401))

        def offline(request, timeout=None):
            raise urllib.error.URLError("no route")
        with self.assertRaises(ai_providers.AiError) as said:
            ai_providers.ask_openai("compatible", "", "m", "http://localhost:1234/v1", PLAN_REQUEST, 45, offline)
        self.assertIn("Can't reach m", str(said.exception))

    def test_speech_to_text_with_chatgpt(self):
        asked = []

        def opener(request, timeout=None):
            asked.append(request)
            if b"gpt-4o-mini-transcribe" in request.data:
                raise urllib.error.HTTPError(request.full_url, 404, "no", {}, io.BytesIO(b"{}"))
            return FakeAnswer(b'{"text": " open gmail "}')
        self.assertEqual(ai_providers.transcribe_openai("sk-test", b"RIFF....", "audio/wav", opener=opener), "open gmail")
        self.assertEqual(len(asked), 2)  # the newer model wasn't there, so whisper-1
        self.assertIn(b'filename="speech.wav"', asked[1].data)
        self.assertTrue(asked[1].get_header("Content-type").startswith("multipart/form-data; boundary="))

    def test_a_keys_models(self):
        openai = answered({"data": [{"id": "dall-e-3"}, {"id": "gpt-4o"}, {"id": "gpt-5-mini"}, {"id": "o4-mini"}]})
        models = ai_providers.list_models("openai", "sk-test", opener=openai)
        self.assertEqual(models[:2], ["gpt-5-mini", "gpt-4o"])  # the quick ones that see pictures first
        self.assertEqual(ai_providers.suggested_model("openai", models), "gpt-5-mini")
        gemini = answered({"models": [{"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
                                      {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]})
        self.assertEqual(ai_providers.list_models("gemini", "AIza-test", opener=gemini), ["gemini-3.5-flash"])
        claude = FakeClaude()
        models = ai_providers.list_models("claude", "sk-ant-test", client_factory=claude)
        self.assertEqual(ai_providers.suggested_model("claude", models), "claude-opus-5-5")


class ChosenAiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, True)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp})
        patcher.start()
        self.addCleanup(patcher.stop)

    def choose(self, provider, key="", model="", base=""):
        app.save_setting("ai_provider", provider)
        if key:
            app.save_key(ai_providers.PROVIDERS[provider]["key_name"], key)
        if model:
            app.save_setting("ai_models", {provider: model})
        if base:
            app.save_setting("ai_base_url", base)

    def test_gemini_unless_another_is_chosen(self):
        self.assertEqual((app.ai_provider(), app.ai_ready(), app.ai_name()), ("gemini", False, "Gemini"))
        self.choose("claude", key="sk-ant-test")
        self.assertEqual((app.ai_model(), app.ai_ready(), app.ai_name()), ("claude-opus-5-5", True, "Claude"))
        self.choose("compatible", model="llama3.2-vision", base="http://localhost:11434/v1")
        self.assertTrue(app.ai_ready())  # no key needed on this PC
        self.assertEqual(app.ai_name(), "llama3.2-vision")
        app.save_setting("ai_provider", "something-else")
        self.assertEqual(app.ai_provider(), "gemini")

    def test_every_request_goes_to_the_chosen_ai(self):
        read = lambda data, model: (data["candidates"][0]["content"]["parts"][0]["text"], model)
        with mock.patch.object(app, "ask_gemini", return_value="from gemini") as gemini:
            self.assertEqual(app.ask_ai(PLAN_REQUEST, read, api_key="AIza-test"), "from gemini")
        self.assertEqual(gemini.call_args.args[1], "AIza-test")
        self.choose("claude", key="sk-ant-test", model="claude-sonnet-5-5")
        with mock.patch.object(ai_providers, "ask_claude", return_value=ai_providers.gemini_shape("hi")) as claude:
            self.assertEqual(app.ask_ai(PLAN_REQUEST, read), ("hi", "claude-sonnet-5-5"))
        self.assertEqual(claude.call_args.args[:3], ("sk-ant-test", "claude-sonnet-5-5", PLAN_REQUEST))
        with mock.patch.object(ai_providers, "ask_claude", side_effect=ai_providers.AiError("Claude didn't accept the key.")):
            with self.assertRaises(RuntimeError) as said:
                app.ask_ai(PLAN_REQUEST, read)
        self.assertEqual(str(said.exception), "Claude didn't accept the key.")

    def test_speech_with_every_ai(self):
        self.choose("claude", key="sk-ant-test")
        with mock.patch.object(app, "windows_dictation", return_value="open gmail") as windows:
            self.assertEqual(app.transcribe_speech(b"RIFF", "k"), "open gmail")
        windows.assert_called_once_with(b"RIFF")
        with self.assertRaises(RuntimeError) as said:  # a Telegram voice message
            app.transcribe_speech(b"OggS", "k", "audio/ogg")
        self.assertIn("need Gemini or ChatGPT", str(said.exception))
        self.choose("openai", key="sk-test")
        with mock.patch.object(ai_providers, "transcribe_openai", return_value="pause the music"):
            self.assertEqual(app.transcribe_speech(b"RIFF", "k"), "pause the music")
        with mock.patch.object(ai_providers, "transcribe_openai", side_effect=ai_providers.AiError("busy", 429)), \
                mock.patch.object(app, "windows_dictation", return_value="pause") as windows:
            self.assertEqual(app.transcribe_speech(b"RIFF", "k"), "pause")  # Windows steps in

    def test_no_ai_yet(self):
        said = app._execute_smart_action_locked("open the top email", mock.Mock(), 1000, 800)
        self.assertIn("Claude, ChatGPT", said)

    def test_research_with_an_ai_that_cant_open_pages(self):
        self.choose("claude", key="sk-ant-test")
        pages = [("Projects | Claude", "https://claude.com/projects", "About projects"),
                 ("Unreadable", "https://example.com/blocked", "")]
        asked = {}

        def ask(payload, read, *args, **kwargs):
            asked["payload"] = payload
            return read(ai_providers.gemini_shape(ANSWER_TEXT), "claude-opus-5-5")
        with mock.patch.object(app, "web_search", return_value=("Bing", pages)), \
                mock.patch.object(app, "read_web_pages", return_value={"https://claude.com/projects": "Projects hold chats."}), \
                mock.patch.object(app, "ask_ai", side_effect=ask):
            card = app.research("claude projects", "")
        self.assertNotIn("tools", asked["payload"])  # no Gemini URL tool
        self.assertIn("Projects hold chats.", asked["payload"]["contents"][0]["parts"][0]["text"])
        self.assertEqual([s["url"] for s in card["sources"]], ["https://claude.com/projects"])

    def test_pages_are_read_only_from_the_internet(self):
        public = [(2, 1, 6, "", ("142.250.80.46", 443))]
        private = [(2, 1, 6, "", ("192.168.1.1", 80))]
        with mock.patch.object(app.socket, "getaddrinfo", return_value=public):
            self.assertTrue(app.public_web_address("https://example.com/page"))
        with mock.patch.object(app.socket, "getaddrinfo", return_value=private):
            self.assertFalse(app.public_web_address("http://router.local/admin"))  # a search result can't send it home
        self.assertFalse(app.public_web_address("file:///C:/Windows/win.ini"))
        page = b"<html><head><style>.x{}</style><script>alert(1)</script></head><body><h1>Hello</h1><p>A &amp; B</p></body></html>"
        answer = FakeAnswer(page)
        answer.headers = mock.Mock(**{"get.return_value": "text/html", "get_content_charset.return_value": "utf-8"})
        with mock.patch.object(app, "public_web_address", return_value=True), \
                mock.patch.object(app.urllib.request, "urlopen", return_value=answer):
            self.assertEqual(app.read_web_page("https://example.com"), "Hello A & B")


class AiDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, True)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dialog = app.AiDialog()
        self.addCleanup(self.dialog.deleteLater)

    def pick(self, provider):
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData(provider))

    def test_choosing_claude(self):
        self.pick("claude")
        self.assertEqual(self.dialog.model.currentText(), "claude-opus-5-5")
        self.dialog.key.setText("  sk-ant-api03-test  ")
        self.dialog.save()
        self.assertEqual((app.ai_provider(), app.ai_key(), app.ai_model()), ("claude", "sk-ant-api03-test", "claude-opus-5-5"))

    def test_a_service_needs_a_safe_address(self):
        self.pick("compatible")
        self.dialog.model.setCurrentText("llama3.2-vision")
        self.dialog.base_url.setText("http://example.com/v1")
        self.dialog.save()
        self.assertIn("https://", self.dialog.status.text())  # the key would cross the internet unencrypted
        self.assertEqual(app.ai_provider(), "gemini")
        self.dialog.base_url.setText("http://localhost:11434/v1")
        self.dialog.save()
        self.assertEqual((app.ai_provider(), app.ai_base_url(), app.ai_model()),
                         ("compatible", "http://localhost:11434/v1", "llama3.2-vision"))

    def test_check_fills_in_the_models(self):
        self.pick("openai")
        self.dialog.show_check("openai", ["gpt-5-mini", "gpt-4o"], "✓  The key works (2 models).")
        self.assertEqual(self.dialog.model.currentText(), "gpt-5-mini")
        self.dialog.show_check("claude", ["claude-opus-5-5"], "late")  # about an AI no longer picked
        self.assertNotEqual(self.dialog.status.text(), "late")
        self.pick("gemini")
        self.dialog.save()
        self.assertIn("Paste your key", self.dialog.status.text())


if __name__ == "__main__":
    unittest.main()
