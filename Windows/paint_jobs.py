# Paint jobs: each colours the helmet's shell and the bike's bodywork together. The first is the original
# blacked-out look; the others are the classic race colours. Every colour runs from the lit top to the
# shadowed bottom.
import re

PAINTS = {
    "midnight": {
        "label": "Midnight glitter", "swatch": "#2B2F39",
        "shell": ("#3C414E", "#1A1D24", "#0A0B0F", "#020203"), "panel": ("#2E333E", "#15171D", "#07080A"),
        "jaw": ("#3A3F4B", "#15171C"), "chin": ("#2B2F39", "#121419", "#050506"),
        "gloss": ("#363B47", "#15171D", "#040405"), "side": ("#2C313C", "#0E1014", "#050506"),
    },
    "lime": {
        "label": "Kawasaki green", "swatch": "#6CC417",
        "shell": ("#D2F77A", "#7BD320", "#3F8C0C", "#143303"), "panel": ("#5AB01A", "#2F6E0A", "#0F2A03"),
        "jaw": ("#3A3F4B", "#15171C"), "chin": ("#2B2F39", "#121419", "#050506"),
        "gloss": ("#B4EE5A", "#5BB315", "#1A4204"), "side": ("#7BD320", "#3F8C0C", "#0F2A03"),
    },
    "red": {
        "label": "Racing red", "swatch": "#D61F26",
        "shell": ("#FF9A90", "#E3262E", "#97101A", "#3A0307"), "panel": ("#C81E28", "#7E0C13", "#2E0306"),
        "jaw": ("#3A3F4B", "#15171C"), "chin": ("#2B2F39", "#121419", "#050506"),
        "gloss": ("#FF7A74", "#D61F26", "#54060B"), "side": ("#E3262E", "#97101A", "#2E0306"),
    },
    "white": {
        "label": "Arctic white", "swatch": "#E6EAF0",
        "shell": ("#FFFFFF", "#EEF1F6", "#C3CAD6", "#6E7689"), "panel": ("#E6EAF0", "#B5BDCA", "#7C8496"),
        "jaw": ("#D9DEE7", "#8F98A9"), "chin": ("#C9CFDA", "#9AA2B2", "#4A5160"),
        "gloss": ("#FFFFFF", "#DDE2EA", "#808A9E"), "side": ("#EEF1F6", "#B5BDCA", "#5B6272"),
    },
    "blue": {
        "label": "Electric blue", "swatch": "#2F7BEA",
        "shell": ("#A6DAFF", "#2F86F0", "#1449B0", "#061B4D"), "panel": ("#2470D8", "#0F3E96", "#04163F"),
        "jaw": ("#3A3F4B", "#15171C"), "chin": ("#2B2F39", "#121419", "#050506"),
        "gloss": ("#86C9FF", "#2F7BEA", "#0B2A6B"), "side": ("#2F86F0", "#1449B0", "#04163F"),
    },
    "gold": {
        "label": "Gold flake", "swatch": "#E8B04B",
        "shell": ("#FFF1B8", "#F2C14E", "#A87414", "#3A2604"), "panel": ("#D9A23A", "#8E5F0E", "#2E1E03"),
        "jaw": ("#3A3F4B", "#15171C"), "chin": ("#2B2F39", "#121419", "#050506"),
        "gloss": ("#FFE08A", "#E2A93A", "#5E3E07"), "side": ("#F2C14E", "#A87414", "#2E1E03"),
    },
}
DEFAULT = "midnight"
_current = {"name": DEFAULT}


def current() -> str:
    return _current["name"]


def set_current(name: str) -> bool:
    # Whether the paint changed (an unknown name changes nothing).
    if name not in PAINTS or name == _current["name"]:
        return False
    _current["name"] = name
    return True


def paint(name: str = None) -> dict:
    return PAINTS.get(name or _current["name"], PAINTS[DEFAULT])


NICKNAMES = {"black": "midnight", "dark": "midnight", "original": "midnight", "krt": "lime", "ninja": "lime",
             "silver": "white", "yellow": "gold", "golden": "gold", "navy": "blue"}
FILLER = {"the", "a", "an", "in", "to", "it", "its", "color", "colour", "colors", "colours", "paint", "job", "one", "please",
          "now", "with", "of", "my", "your", "back", "again", "new", "look", "bike", "helmet", "both", "and"}


def find(words: str, strict: bool = False):
    # The paint job some words name: "green", "kawasaki", "the red one", "arctic white"... With strict, every
    # other word must be a small one, so "paint a picture of a red car" names none.
    said = re.findall(r"[a-z]+", str(words).lower())
    vocabulary = dict(NICKNAMES)
    for name, job in PAINTS.items():
        for word in [name] + job["label"].lower().split():
            vocabulary.setdefault(word, name)
    named = [vocabulary[word] for word in said if word in vocabulary]
    if not named or (strict and any(word not in vocabulary and word not in FILLER for word in said)):
        return None
    return max(dict.fromkeys(named), key=named.count)


def stops(colours, offsets) -> str:
    return "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in zip(offsets, colours))
