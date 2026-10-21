"""Shared lists (shopping, to do...): added to from the phone's Lists panel or by saying or sending "add milk to the
shopping list", by the owner or a guest, and ticked off, removed or read back the same ways."""
import json
import re
import threading
import time

ADD = re.compile(r"(?:please )?(?:add|put) (?P<what>.+?) (?:to|on|onto) (?:my |the |our )?(?P<list>[\w' ]{1,30}?) ?list")
REMOVE = re.compile(r"(?:please )?(?:remove|delete|take) (?P<what>.+?) (?:from|off) (?:my |the |our )?(?P<list>[\w' ]{1,30}?) ?list")
DONE = re.compile(r"(?:check|tick|cross) (?:off )?(?P<what>.+?)(?: off)?(?: (?:on|from) (?:my |the |our )?(?P<list>[\w' ]{1,30}?) ?list)?")
READ = re.compile(r"(?:what(?:'s| is) on |read |show (?:me )?)(?:my |the |our )?(?P<list>[\w' ]{1,30}?) ?list|"
                  r"(?:my |the |our )?(?P<list2>[\w']+(?: [\w']+){0,2}?) ?list")
CLEAR = re.compile(r"(?:clear|empty) (?:my |the |our )?(?P<list>[\w' ]{1,30}?) ?list")
SPLIT = re.compile(r"\s*(?:,|\band\b)\s*")
NAMES = {"shopping": "Shopping", "grocery": "Shopping", "groceries": "Shopping", "to do": "To do", "todo": "To do",
         "to-do": "To do", "chores": "Chores"}
MOST_ITEMS = 200


def list_name(said: str) -> str:
    said = " ".join(str(said).lower().split())
    return NAMES.get(said, said[:1].upper() + said[1:]) if said else "To do"


class SharedLists:
    def __init__(self, path):
        self._path = path  # a function giving lists.json's path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with open(self._path(), encoding="utf-8") as f:
                found = json.load(f)
            return found if isinstance(found, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, lists: dict):
        with open(self._path(), "w", encoding="utf-8") as f:
            json.dump(lists, f)

    def all(self) -> dict:
        with self._lock:
            lists = self._load()
            return lists if lists else {"Shopping": [], "To do": []}

    def add(self, name: str, texts: list, by: str = "") -> list:
        with self._lock:
            lists = self._load() or {"Shopping": [], "To do": []}
            items = lists.setdefault(name, [])
            added = []
            for text in texts:
                text = " ".join(str(text).split())[:120]
                if text and not any(i["text"].lower() == text.lower() and not i["done"] for i in items) and len(items) < MOST_ITEMS:
                    items.append({"text": text, "done": False, "by": by, "time": int(time.time())})
                    added.append(text)
            self._save(lists)
            return added

    def change(self, name: str, text: str, done: bool = None, remove: bool = False) -> bool:
        with self._lock:
            lists = self._load()
            items = lists.get(name, [])
            for item in items:
                if item["text"].lower() == text.lower() or (len(text) > 2 and text.lower() in item["text"].lower()):
                    if remove:
                        items.remove(item)
                    else:
                        item["done"] = not item["done"] if done is None else bool(done)
                    self._save(lists)
                    return True
            return False

    def clear(self, name: str, done_only: bool = False) -> int:
        with self._lock:
            lists = self._load()
            items = lists.get(name, [])
            kept = [i for i in items if done_only and not i["done"]]
            gone = len(items) - len(kept)
            if name in lists:
                lists[name] = kept
                self._save(lists)
            return gone


def items_words(name: str, items: list) -> str:
    left = [i["text"] for i in items if not i["done"]]
    if not left:
        return f"The {name.lower()} list is empty."
    return f"{name} ({len(left)}): " + ", ".join(left) + "."


def handle(text: str, book: SharedLists, by: str = ""):
    """The answer to a list command, or None for anything else."""
    said = " ".join(str(text).lower().replace("’", "'").split()).strip(" .!?")
    found = ADD.fullmatch(said)
    if found:
        name = list_name(found["list"])
        added = book.add(name, [w for w in SPLIT.split(found["what"]) if w], by)
        return f"Added {', '.join(added)} to the {name.lower()} list." if added else f"That's already on the {name.lower()} list."
    found = REMOVE.fullmatch(said)
    if found:
        name = list_name(found["list"])
        return (f"Took {found['what']} off the {name.lower()} list." if book.change(name, found["what"], remove=True)
                else f"{found['what'][:1].upper() + found['what'][1:]} isn't on the {name.lower()} list.")
    found = CLEAR.fullmatch(said)
    if found:
        name = list_name(found["list"])
        book.clear(name)
        return f"The {name.lower()} list is empty now."
    found = DONE.fullmatch(said)
    if found and found["list"]:
        name = list_name(found["list"])
        if book.change(name, found["what"], done=True):
            return f"Ticked off {found['what']} on the {name.lower()} list."
        return f"{found['what'][:1].upper() + found['what'][1:]} isn't on the {name.lower()} list."
    found = READ.fullmatch(said)
    if found:
        name = list_name(found["list"] or found["list2"])
        if name in book.all():
            return items_words(name, book.all()[name])
        return None if not found["list"] else f"There's no {name.lower()} list yet."
    return None
