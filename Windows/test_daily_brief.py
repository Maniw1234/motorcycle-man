import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import time
import unittest

import daily_brief

NOW = time.mktime((2026, 10, 20, 8, 30, 0, 0, 0, -1))  # Tuesday morning

PLACES = {"results": [
    {"name": "Springfield", "admin1": "Missouri", "country": "United States", "country_code": "US", "latitude": 37.2, "longitude": -93.3},
    {"name": "Springfield", "admin1": "Massachusetts", "country": "United States", "country_code": "US", "latitude": 42.1015,
     "longitude": -72.5898}]}
FORECAST = {"current": {"temperature_2m": 51.6, "apparent_temperature": 45.2, "weather_code": 2, "wind_speed_10m": 9.4},
            "daily": {"weather_code": [61], "temperature_2m_max": [63.4], "temperature_2m_min": [44.0],
                      "precipitation_probability_max": [70], "sunset": ["2026-10-20T18:05"]}}


class FakeWeb:
    def __init__(self):
        self.asked = []

    def __call__(self, url, params):
        self.asked.append((url, params))
        if url == daily_brief.GEOCODE:
            return PLACES if params["name"].lower() == "springfield" else {}
        return FORECAST


class BriefTests(unittest.TestCase):
    def test_finding_the_town(self):
        web = FakeWeb()
        self.assertEqual(daily_brief.find_place("Springfield, MA", web), {"name": "Springfield, Massachusetts", "lat": 42.1015,
                                                                          "lon": -72.5898})
        self.assertEqual(daily_brief.find_place("springfield ma", web)["name"], "Springfield, Massachusetts")
        self.assertEqual(daily_brief.find_place("Springfield", web)["name"], "Springfield, Missouri")
        self.assertIsNone(daily_brief.find_place("Atlantis", web))

    def test_the_weather_in_words_and_kept_a_while(self):
        web = FakeWeb()
        weather = daily_brief.Weather(web)
        place = {"name": "Springfield, Massachusetts", "lat": 42.1, "lon": -72.6}
        forecast = weather.today(place, us_units=True, now=NOW)
        self.assertEqual((forecast["now"], forecast["high"], forecast["rain"], forecast["wind_unit"]), (52, 63, 70, "mph"))
        self.assertEqual(web.asked[-1][1]["temperature_unit"], "fahrenheit")
        weather.today(place, us_units=True, now=NOW + 60)
        self.assertEqual(len(web.asked), 1)  # kept for ten minutes
        weather.today(place, us_units=False, now=NOW + 60)
        self.assertNotIn("temperature_unit", web.asked[-1][1])
        self.assertEqual(daily_brief.weather_words(forecast, place),
                         "⛅ 52° and partly cloudy in Springfield (feels like 45°). Today: up to 63°, down to 44°, rain likely "
                         "(70%): take an umbrella.")

    def test_the_brief(self):
        nine, six = NOW + 1800, time.mktime((2026, 10, 20, 18, 0, 0, 0, 0, -1))
        text = daily_brief.brief(NOW, "☀️ 60° and clear.", [(six - 3600, "call the dentist"), (nine, "pay rent")],
                                 [(six, "Evening")], "Battery 25%: plug in soon.")
        self.assertEqual(text, "Good morning! It's Tuesday, October 20. ☀️ 60° and clear. Today: 9:00 AM pay rent; 5:00 PM call "
                               "the dentist. Your Evening routine runs at 6:00 PM. Battery 25%: plug in soon.")
        self.assertIn("No reminders today.", daily_brief.brief(NOW, "", [], []))
        self.assertTrue(daily_brief.brief(NOW + 6 * 3600, "", [], []).startswith("Good afternoon!"))

    def test_once_each_morning_when_someone_is_there(self):
        self.assertTrue(daily_brief.time_for_morning_brief(NOW, 3, "2026-10-19", True))
        self.assertFalse(daily_brief.time_for_morning_brief(NOW, 3, "2026-10-20", True))  # already today
        self.assertFalse(daily_brief.time_for_morning_brief(NOW, 600, "2026-10-19", True))  # nobody at the PC
        self.assertFalse(daily_brief.time_for_morning_brief(NOW, 3, "2026-10-19", False))  # switched off
        self.assertFalse(daily_brief.time_for_morning_brief(NOW + 5 * 3600, 3, "2026-10-19", True))  # 1:30 PM
        self.assertFalse(daily_brief.time_for_morning_brief(NOW - 5 * 3600, 3, "2026-10-19", True))  # 3:30 AM


if __name__ == "__main__":
    unittest.main()
