# The welcome tour and the feature guide. Their words live in shared/guide.json, which the Android app reads too
# (Guide.java), so the PC and the phone explain things the same way. Motorcycle Man.exe carries the file
# (MotorcycleMan.spec); from the source it is read from shared/ beside Windows/.
import html
import json
import os

FILE = "guide.json"


def guide_path(resource_path) -> str:
    bundled = resource_path(FILE)
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", FILE)


def load(path: str) -> dict:
    # {"tour": {"pc": [slides], "phone": [slides]}, "features": [...]}, with whatever is missing or unreadable left
    # out, so a broken file never stops Motorcycle Man from starting.
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    tour = data.get("tour") if isinstance(data.get("tour"), dict) else {}
    slides = {where: [s for s in tour.get(where) or [] if isinstance(s, dict) and s.get("title")][:5]
              for where in ("pc", "phone")}
    features = [f for f in data.get("features") or [] if isinstance(f, dict) and f.get("title")]
    return {"tour": slides, "features": features}


def words_of(feature: dict) -> str:
    use = feature.get("use") or []
    return " ".join([str(feature.get(k, "")) for k in ("title", "where", "what", "how", "needs")]
                    + [str(step) for step in use]).lower()


def matches(feature: dict, search: str) -> bool:
    # Every word searched for is somewhere in the feature.
    text = words_of(feature)
    return all(word in text for word in search.lower().split())


def feature_html(feature: dict) -> str:
    # A feature's details for Qt's text view: what it does, how to use it, how it works and what it needs.
    e = lambda key: html.escape(str(feature.get(key, "")))  # noqa: E731
    parts = [f"<h2>{e('icon')} {e('title')}</h2>"]
    if feature.get("where"):
        parts.append(f"<p class='where'>{e('where')}</p>")
    if feature.get("what"):
        parts.append(f"<p>{e('what')}</p>")
    use = [str(step) for step in feature.get("use") or []]
    if use:
        parts.append("<h3>HOW TO USE IT</h3><ul>" + "".join(f"<li>{html.escape(step)}</li>" for step in use) + "</ul>")
    if feature.get("how"):
        parts.append(f"<h3>HOW IT WORKS</h3><p>{e('how')}</p>")
    if feature.get("needs"):
        parts.append(f"<h3>YOU NEED</h3><p>{e('needs')}</p>")
    return "".join(parts)


PAGE = "guide.html"  # the phone's tour and guide (the Android app shows the same page)


def page_path(resource_path) -> str:
    bundled = resource_path(PAGE)
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shared", PAGE)


def phone_page(template: str, data: dict, back: str, mode: str = "guide") -> str:
    # guide.html with the words in place, for a phone's browser; Back goes to back. Every value goes in as JSON
    # with "</" broken up, so nothing in it can end the page's script.
    def js(value) -> str:
        return json.dumps(value, ensure_ascii=True).replace("</", "<\\/")
    return (template.replace("/*GUIDE_JSON*/{}", js({"tour": data["tour"], "features": data["features"]}), 1)
            .replace('/*MODE*/"guide"', js(mode), 1).replace('/*BACK*/""', js(back), 1))
