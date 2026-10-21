"""The daily brief: the weather where the user lives, today's reminders and routines, and a word about the PC.

The weather comes from Open-Meteo (free, no key, no account): the town is looked up once with its geocoding
service and kept as a latitude and longitude. Fahrenheit and mph where Windows is set to US measurements.
It's said when asked ("good morning", "what's my day like", "what's the weather") and, if that's on, once
each morning when the user first sits down at the PC.
"""
import datetime as dt
import json
import re
import threading
import time
import urllib.parse
import urllib.request

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
WEATHER = {0: ("☀️", "clear"), 1: ("🌤", "mostly clear"), 2: ("⛅", "partly cloudy"), 3: ("☁️", "cloudy"),
           45: ("🌫", "foggy"), 48: ("🌫", "foggy"), 51: ("🌦", "drizzly"), 53: ("🌦", "drizzly"), 55: ("🌧", "drizzly"),
           56: ("🌧", "freezing drizzle"), 57: ("🌧", "freezing drizzle"), 61: ("🌦", "light rain"), 63: ("🌧", "rainy"),
           65: ("🌧", "heavy rain"), 66: ("🌧", "freezing rain"), 67: ("🌧", "freezing rain"), 71: ("🌨", "light snow"),
           73: ("🌨", "snowy"), 75: ("❄️", "heavy snow"), 77: ("🌨", "snowy"), 80: ("🌦", "showers"), 81: ("🌧", "showers"),
           82: ("⛈", "heavy showers"), 85: ("🌨", "snow showers"), 86: ("❄️", "heavy snow showers"), 95: ("⛈", "thunderstorms"),
           96: ("⛈", "thunderstorms with hail"), 99: ("⛈", "thunderstorms with hail")}
US_STATES = {"al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
             "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
             "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana", "me": "maine",
             "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri",
             "mt": "montana", "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
             "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
             "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee",
             "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
             "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia"}
MORNING = (5, 12)  # the hours a morning brief can come in
FRESH_FOR = 600  # seconds a forecast is kept


def fetch_json(url: str, params: dict, timeout: float = 8.0):
    request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "MotorcycleMan/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read(1 << 20).decode("utf-8"))


def find_place(name: str, fetch=fetch_json):
    """{"name": "Springfield, Massachusetts", "lat", "lon"} for a town ("Springfield, MA" picks the right one), or None."""
    parts = [p.strip() for p in re.split(r",", str(name)) if p.strip()]
    if not parts or len(parts[0]) > 80:
        return None
    found = (fetch(GEOCODE, {"name": parts[0], "count": 10, "language": "en", "format": "json"}) or {}).get("results") or []
    if not found and len(parts) == 1 and " " in parts[0]:  # "springfield ma": the last word may be the state
        return find_place(", ".join(parts[0].rsplit(" ", 1)), fetch)
    if not found:
        return None
    pick = found[0]
    if len(parts) > 1:
        want = parts[1].lower()
        want = US_STATES.get(want, want)
        pick = next((r for r in found if want in (str(r.get("admin1", "")).lower(), str(r.get("country", "")).lower(),
                                                  str(r.get("country_code", "")).lower())), pick)
    region = pick.get("admin1") or pick.get("country") or ""
    label = pick["name"] + (f", {region}" if region and region != pick["name"] else "")
    return {"name": label, "lat": round(float(pick["latitude"]), 4), "lon": round(float(pick["longitude"]), 4)}


class Weather:
    """The forecast for a place, kept for ten minutes."""

    def __init__(self, fetch=fetch_json):
        self._fetch, self._kept, self._lock = fetch, {}, threading.Lock()

    def today(self, place: dict, us_units: bool, now: float = None) -> dict:
        now = time.time() if now is None else now
        key = (place["lat"], place["lon"], us_units)
        with self._lock:
            kept = self._kept.get(key)
            if kept and now - kept[0] < FRESH_FOR:
                return kept[1]
        params = {"latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 1,
                  "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunset"}
        if us_units:
            params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
        data = self._fetch(FORECAST, params)
        current, daily = data["current"], data["daily"]
        forecast = {"now": round(current["temperature_2m"]), "feels": round(current["apparent_temperature"]),
                    "code": int(current["weather_code"]), "wind": round(current["wind_speed_10m"]),
                    "high": round(daily["temperature_2m_max"][0]), "low": round(daily["temperature_2m_min"][0]),
                    "rain": int(daily["precipitation_probability_max"][0] or 0), "day_code": int(daily["weather_code"][0]),
                    "sunset": str(daily["sunset"][0])[-5:], "wind_unit": "mph" if us_units else "km/h"}
        with self._lock:
            self._kept[key] = (now, forecast)
        return forecast


def weather_words(forecast: dict, place: dict) -> str:
    icon, sky = WEATHER.get(forecast["code"], ("🌡", "out there"))
    town = place["name"].split(",")[0]
    feels = f" (feels like {forecast['feels']}°)" if abs(forecast["feels"] - forecast["now"]) >= 5 else ""
    rain = forecast["rain"]
    wet = f"rain likely ({rain}%): take an umbrella" if rain >= 60 else f"maybe some rain ({rain}%)" if rain >= 30 else \
        "no rain expected"
    return (f"{icon} {forecast['now']}° and {sky} in {town}{feels}. Today: up to {forecast['high']}°, down to "
            f"{forecast['low']}°, {wet}.")


def greeting(now: float) -> str:
    hour = dt.datetime.fromtimestamp(now).hour
    return "Good morning" if 4 <= hour < 12 else "Good afternoon" if 12 <= hour < 17 else "Good evening"


def today_end(now: float) -> float:
    return (dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)).timestamp()


def brief(now: float, weather_text: str, reminders_today: list, routines_today: list, pc_text: str = "") -> str:
    """reminders_today and routines_today: [(due, text or routine name)]."""
    day = dt.datetime.fromtimestamp(now)
    parts = [f"{greeting(now)}! It's {day.strftime('%A, %B')} {day.day}."]
    if weather_text:
        parts.append(weather_text)
    def at(due):
        return dt.datetime.fromtimestamp(due).strftime("%I:%M %p").lstrip("0")
    if reminders_today:
        parts.append("Today: " + "; ".join(f"{at(due)} {text}" for due, text in sorted(reminders_today)) + ".")
    else:
        parts.append("No reminders today.")
    for due, name in sorted(routines_today):
        parts.append(f"Your {name} routine runs at {at(due)}.")
    if pc_text:
        parts.append(pc_text)
    return " ".join(parts)


def time_for_morning_brief(now: float, idle_seconds: float, last_day: str, on: bool) -> bool:
    """Once a day, in the morning, as soon as someone is at the PC."""
    moment = dt.datetime.fromtimestamp(now)
    return bool(on and MORNING[0] <= moment.hour < MORNING[1] and last_day != moment.date().isoformat() and idle_seconds < 30)
