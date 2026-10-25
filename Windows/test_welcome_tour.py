import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import guide
import motorcycle_man as app

QT = QApplication.instance() or QApplication([])
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = "tour-token"


def wait(seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QT.processEvents()
        time.sleep(0.02)


class TempAppData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class GuideFileTests(unittest.TestCase):
    def test_the_shared_words(self):
        data = guide.load(guide.guide_path(app.resource_path))
        self.assertEqual((len(data["tour"]["pc"]), len(data["tour"]["phone"])), (5, 5))  # five slides or fewer
        for slide in data["tour"]["pc"] + data["tour"]["phone"]:
            self.assertTrue(slide["icon"] and slide["title"] and slide["text"])
            self.assertLess(len(slide["text"].split()), 50, slide["title"])  # a slide, not a page
        self.assertGreaterEqual(len(data["features"]), 25)
        for feature in data["features"]:
            self.assertTrue(feature.get("what") and feature.get("use") and feature.get("how"), feature["title"])
        titles = " ".join(f["title"] for f in data["features"]).lower()
        for name in ("commands", "screen", "touchpad", "files", "alerts", "reminders", "guard", "updates"):
            self.assertIn(name, titles)

    def test_a_broken_file_never_stops_motorcycle_man(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "guide.json")
            empty = {"tour": {"pc": [], "phone": []}, "features": []}
            self.assertEqual(guide.load(path), empty)  # missing
            for text in ("{not json", "[1, 2]", '{"tour": 5, "features": "x"}'):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self.assertEqual(guide.load(path), empty, text)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tour": {"pc": [{"title": str(i)} for i in range(8)]}}, f)
            self.assertEqual(len(guide.load(path)["tour"]["pc"]), 5)  # never more than five

    def test_search_and_details(self):
        feature = {"icon": "📁", "title": "Files", "where": "Phone menu", "what": "Send <b>files</b>",
                   "use": ["Menu > Send"], "how": "Over your link", "needs": "Nothing"}
        self.assertTrue(guide.matches(feature, "send LINK"))
        self.assertTrue(guide.matches(feature, ""))
        self.assertFalse(guide.matches(feature, "music"))
        details = guide.feature_html(feature)
        self.assertIn("HOW TO USE IT", details)
        self.assertIn("&lt;b&gt;files&lt;/b&gt;", details)  # words, never markup
        self.assertIn("&gt; Send", details)

    def test_the_phone_page_keeps_its_script_whole(self):
        with open(guide.page_path(app.resource_path), encoding="utf-8") as f:
            template = f.read()
        data = {"tour": {"pc": [], "phone": []},
                "features": [{"title": "</script><script>alert(1)</script>", "what": "x", "use": [], "how": "y"}]}
        page = guide.phone_page(template, data, "/?token=abc", "tour")
        self.assertEqual(page.count("</script>"), template.count("</script>"))
        self.assertIn('let mode = "tour";', page)
        self.assertIn('const BACK = "/?token=abc";', page)
        self.assertNotIn("/*GUIDE_JSON*/", page)


class TourDialogTests(TempAppData):
    def dialog(self, slides, open_guide=None):
        dialog = app.TourDialog(None, slides, open_guide or mock.Mock())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # never on the real screen
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        return dialog

    def test_five_slides_with_next_back_and_skip(self):
        slides = app.guide_data()["tour"]["pc"]
        dialog = self.dialog(slides)
        self.assertEqual((dialog.title.text().split("  ", 1)[1], dialog.skip.text(), dialog.back.isVisible()),
                         (slides[0]["title"], "Skip", False))
        for _ in range(10):
            dialog.next.click()
        self.assertEqual(dialog.at, 4)  # stays on the last one... until Let's go! closes it
        dialog.show()
        dialog.show_slide(4)
        self.assertEqual((dialog.next.text(), dialog.skip.text()), ("Let's go!", "See every feature"))
        dialog.back.click()
        self.assertEqual(dialog.at, 3)

    def test_the_last_slide_opens_the_guide(self):
        open_guide = mock.Mock()
        dialog = self.dialog(app.guide_data()["tour"]["pc"], open_guide)
        dialog.skip.click()  # Skip on the first slide: just closes
        open_guide.assert_not_called()
        dialog.show()
        dialog.show_slide(4)
        dialog.skip.click()
        open_guide.assert_called_once_with()
        self.assertFalse(dialog.isVisible())

    def test_no_slides_still_says_hello(self):
        dialog = self.dialog([])
        self.assertIn("Motorcycle Man", dialog.title.text())


class OnceOnlyTests(TempAppData):
    def window(self, tour=True):
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token="test-token")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.tray = mock.Mock()
        window.welcome_tour = tour
        self.addCleanup(window.deleteLater)
        return window

    def test_the_tour_comes_the_first_time_only(self):
        with mock.patch.object(app, "TourDialog") as tour:
            first = self.window()
            first.show()
            wait(0.8)
            self.assertEqual(tour.call_count, 1)
            self.assertTrue(app.load_settings().get("tour_seen"))
            first.hide()
            first.show()  # opened again from the tray
            wait(0.8)
            later = self.window()  # Motorcycle Man started again, or after an update
            later.show()
            wait(0.8)
        self.assertEqual(tour.call_count, 1)

    def test_tests_and_hidden_starts_get_no_tour(self):
        with mock.patch.object(app, "TourDialog") as tour:
            self.window(tour=False).show()
            self.window()  # started by the clock (--tray): the window isn't shown
            wait(0.8)
        tour.assert_not_called()
        self.assertFalse(app.load_settings().get("tour_seen", False))

    def test_the_guide_button_and_the_tour_again(self):
        window = self.window()
        with mock.patch.object(app, "FeatureGuideDialog") as guide_window, mock.patch.object(app, "TourDialog") as tour:
            window.open_guide()
            window.open_guide()  # the same window comes forward
            self.assertEqual(guide_window.call_count, 1)
            features, replay = guide_window.call_args.args[1:]
            self.assertEqual(len(features), len(app.guide_data()["features"]))
            replay()
            tour.assert_called_once()

    def test_the_guide_window_lists_and_finds(self):
        dialog = app.FeatureGuideDialog(None, app.guide_data()["features"], mock.Mock())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.assertEqual(dialog.list.count(), len(app.guide_data()["features"]))
        self.assertIn("HOW TO USE IT", dialog.details.toHtml())
        dialog.search.setText("away from home")
        shown = [dialog.list.item(i).text() for i in range(dialog.list.count()) if not dialog.list.item(i).isHidden()]
        self.assertEqual(len(shown), 1)
        self.assertIn("Internet link", shown[0])
        self.assertIn("Internet link", dialog.details.toPlainText())
        dialog.search.setText("zzzz")
        self.assertIn("Nothing about", dialog.details.toPlainText())

    def test_the_tray_menu_has_the_guide(self):
        from PySide6.QtWidgets import QMenu
        menu, window = QMenu(), mock.Mock()
        app.fill_tray_menu(menu, window, mock.Mock(), 1000, 800)
        guide_item = next(a for a in menu.actions() if a.text().startswith("Guide"))
        guide_item.trigger()
        window.open_guide.assert_called_once_with()


class PhoneGuideTests(TempAppData):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 2560, 1600))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path)
        reply = connection.getresponse()
        data = reply.read().decode("utf-8")
        connection.close()
        return reply.status, data

    def test_the_phone_page_opens_the_guide(self):
        status, page = self.get(f"/guide?token={TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("Everything I can do", page)
        self.assertIn('let mode = "guide";', page)
        self.assertIn(f'const BACK = "/?token={TOKEN}";', page)
        self.assertIn("Touchpad and keyboard", json.loads(page.split("const GUIDE = ", 1)[1].split(";\n", 1)[0])
                      ["features"][9]["title"])
        self.assertIn("openGuide", app.phone_page())  # the menu's Guide tile
        self.assertIn("<span>Guide</span>", app.phone_page())

    def test_not_without_the_link_secret(self):
        status, page = self.get("/guide?token=wrong")
        self.assertEqual(status, 403)
        self.assertNotIn("Everything I can do", page)


class AndroidCopyTests(unittest.TestCase):
    def test_the_phone_app_carries_the_same_files(self):
        with open(os.path.join(HERE, "..", "Android", "build_apk.ps1"), encoding="utf-8") as f:
            script = f.read()
        self.assertIn("..\\shared\\guide.json", script)
        self.assertIn("..\\shared\\guide.html", script)
        self.assertIn('"-A", "$BuildDir\\assets"', script)
        with open(os.path.join(HERE, "..", "Android", "src", "app", "motorcycleman", "remote", "Guide.java"),
                  encoding="utf-8") as f:
            java = f.read()
        with open(guide.page_path(app.resource_path), encoding="utf-8") as f:
            page = f.read()
        for placeholder in ('/*GUIDE_JSON*/{}', '/*MODE*/\\"guide\\"'):  # what Guide.java swaps in the page
            self.assertIn(placeholder, java)
            self.assertIn(placeholder.replace('\\"', '"'), page)


if __name__ == "__main__":
    unittest.main()
