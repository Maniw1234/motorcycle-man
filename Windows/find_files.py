"""Finding files on the PC: by words in their names (or inside documents, where Windows' search index has
read them), what kind they are and when they changed.

understand("find my tax return pdf from last week") -> {"words": ["tax", "return"], "kind": "pdf", "since": ...}
search(query) -> [{"path", "name", "folder", "size", "modified", "is_folder"}], best first.

Windows Search's index answers in a moment and covers the user's folders; when it isn't there (or finds
nothing) the usual folders are looked through by hand, for a few seconds at most.
"""
import datetime as dt
import os
import re
import time
import warnings

KINDS = {
    "pdf": (".pdf",),
    "document": (".doc", ".docx", ".pdf", ".txt", ".odt", ".rtf", ".md", ".pages"),
    "word": (".doc", ".docx", ".odt", ".rtf"),
    "spreadsheet": (".xls", ".xlsx", ".xlsm", ".csv", ".ods", ".numbers"),
    "presentation": (".ppt", ".pptx", ".odp", ".key"),
    "photo": (".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".raw", ".cr2", ".nef", ".dng"),
    "video": (".mp4", ".mov", ".mkv", ".avi", ".wmv", ".webm", ".m4v"),
    "music": (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma"),
    "zip": (".zip", ".rar", ".7z", ".tar", ".gz"),
    "folder": (),
}
KIND_WORDS = {"pdf": "pdf", "document": "document", "doc": "word", "docx": "word", "word": "word", "essay": "document",
              "spreadsheet": "spreadsheet", "excel": "spreadsheet", "xlsx": "spreadsheet", "csv": "spreadsheet", "sheet": "spreadsheet",
              "presentation": "presentation", "powerpoint": "presentation", "pptx": "presentation", "slideshow": "presentation",
              "slides": "presentation", "deck": "presentation", "photo": "photo", "picture": "photo", "pic": "photo",
              "image": "photo", "screenshot": "photo", "jpg": "photo", "png": "photo", "video": "video", "movie": "video",
              "clip": "video", "recording": "video", "mp4": "video", "music": "music", "song": "music", "audio": "music",
              "mp3": "music", "zip": "zip", "archive": "zip", "folder": "folder", "directory": "folder"}
LABELS = {"pdf": "PDFs", "document": "documents", "word": "Word documents", "spreadsheet": "spreadsheets",
          "presentation": "presentations", "photo": "photos", "video": "videos", "music": "music", "zip": "zip files",
          "folder": "folders"}
FILLER = {"my", "the", "a", "an", "all", "some", "any", "file", "files", "called", "named", "name", "with", "that", "which",
          "is", "are", "was", "were", "i", "saved", "downloaded", "made", "of", "for", "from", "on", "in", "pc", "computer",
          "laptop", "this", "me", "please", "where", "it", "one", "ones", "about", "and", "or", "to", "had", "put", "did", "do",
          "latest", "newest", "recent", "last", "week", "month", "today", "yesterday", "there", "find", "search", "look",
          "locate", "show", "get", "can", "you", "could", "go"}
NOISE = re.compile(r"\\(?:appdata|node_modules|\.git|\.gradle|\.vs|\.idea|\.claude|__pycache__|site-packages|\$recycle\.bin|"
                   r"build|dist|obj|bin|cache|caches|temp|tmp|intermediate|deriveddatacache|binaries|venv|\.venv|"
                   r"packages|vendor|target)\\", re.IGNORECASE)
BEST_FOLDERS = ("desktop", "documents", "downloads", "pictures", "onedrive", "videos", "music")
CODE = (".rs", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rb",
        ".php", ".json", ".xml", ".yml", ".yaml", ".toml", ".lock", ".map", ".ini", ".cfg", ".log", ".sln", ".csproj", ".uasset",
        ".rlib", ".iml", ".uproject", ".pdb", ".obj", ".o")
MOST = 15


def understand(text: str, now: float = None) -> dict:
    now = time.time() if now is None else now
    low = " ".join(str(text).lower().replace("’", "'").split())
    since, newest = None, False
    today = dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    for pattern, start in ((r"\btoday\b", today), (r"\byesterday\b", today - dt.timedelta(days=1)),
                           (r"\b(?:this|past) week\b", today - dt.timedelta(days=today.weekday())),
                           (r"\blast week\b", today - dt.timedelta(days=today.weekday() + 7)),
                           (r"\b(?:this|past) month\b", today.replace(day=1)),
                           (r"\blast month\b", (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1))):
        if re.search(pattern, low):
            since = start.timestamp()
            break
    if re.search(r"\b(?:latest|newest|most recent|recent|last)\b", low):
        newest = True
    kind, words = None, []
    for word in re.findall(r"[\w.\-]+", low):
        word = word.strip(".-")
        plain = word[:-1] if word.endswith("s") and word[:-1] in KIND_WORDS else word
        if plain in KIND_WORDS and kind is None:
            kind = KIND_WORDS[plain]
            if plain == "screenshot":  # Windows names them so, too
                words.append(plain)
        elif word and word not in FILLER and (not word.isdigit() or len(word) >= 3):
            words.append(word)
    return {"words": words[:6], "kind": kind, "since": since, "newest": newest or bool(since)}


def describe(query: dict) -> str:
    what = " ".join(query["words"])
    kind = LABELS.get(query["kind"], "files")
    return f"{kind} named “{what}”" if what else kind


def _sql_text(word: str) -> str:
    return re.sub(r"[^\w.\- ]", "", word).replace("'", "''")


def _from_index(query: dict, home: str, limit: int = 200) -> list:
    """[(path, from its name: True, or from inside it: False)], newest first, from Windows Search's index."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import comtypes.client
    connection = comtypes.client.CreateObject("ADODB.Connection")
    connection.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    try:
        where = [f"SCOPE='file:{_sql_text(home).replace(chr(92), '/')}'"]
        words = [_sql_text(w) for w in query["words"] if _sql_text(w)]
        if words:
            in_name = " AND ".join(f"System.FileName LIKE '%{w}%'" for w in words)
            inside = " AND ".join(f'"{w}*"' for w in words)
            where.append(f"(({in_name}) OR CONTAINS(*, '{inside}'))")
        extensions = KINDS.get(query["kind"]) or ()
        if extensions:
            where.append("(" + " OR ".join(f"System.FileExtension = '{e}'" for e in extensions) + ")")
        elif query["kind"] == "folder":
            where.append("System.ItemType = 'Directory'")
        sql = (f"SELECT TOP {limit} System.ItemPathDisplay, System.FileName FROM SystemIndex WHERE {' AND '.join(where)} "
               "ORDER BY System.DateModified DESC")
        rows = comtypes.client.CreateObject("ADODB.Recordset")
        rows.Open(sql, connection)
        found = []
        try:
            while not rows.EOF:
                path, name = str(rows.Fields.Item(0).Value or ""), str(rows.Fields.Item(1).Value or "").lower()
                if path:
                    found.append((path, all(w.lower() in name for w in words)))
                rows.MoveNext()
        finally:
            rows.Close()
        return found
    finally:
        connection.Close()


def usual_folders(home: str) -> list:
    folders = [os.path.join(home, name) for name in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    if os.environ.get("OneDrive"):
        folders.append(os.environ["OneDrive"])
    return [f for f in dict.fromkeys(folders) if os.path.isdir(f)]


def _by_hand(query: dict, home: str, seconds: float = 4.0) -> list:
    """The same as _from_index, looking through the usual folders by name only, for a few seconds at most."""
    end, found = time.monotonic() + seconds, []
    words = [w.lower() for w in query["words"]]
    extensions = KINDS.get(query["kind"]) or ()
    for top in usual_folders(home):
        for folder, subfolders, files in os.walk(top):
            if time.monotonic() > end:
                return found
            subfolders[:] = [s for s in subfolders if not NOISE.search("\\" + s + "\\") and not s.startswith(".")]
            names = subfolders if query["kind"] == "folder" else files
            for name in names:
                low = name.lower()
                if words and not all(w in low for w in words):
                    continue
                if extensions and not low.endswith(extensions):
                    continue
                found.append((os.path.join(folder, name), True))
    return found


def search(query: dict, home: str = None, most: int = MOST) -> list:
    home = home or os.path.expanduser("~")
    if not query["words"] and not query["kind"] and not query["since"]:
        return []
    try:
        found = _from_index(query, home)
    except Exception:
        found = []
    if not found:
        found = _by_hand(query, home)
    results, seen = [], set()
    top = home.rstrip("\\") + "\\"
    for path, by_name in found:
        inside = path[len(top) - 1:] if path.lower().startswith(top.lower()) else path  # "\Documents\..." under home
        if path.lower() in seen or NOISE.search(inside):
            continue
        seen.add(path.lower())
        try:
            info = os.stat(path)
        except OSError:
            continue
        if query["since"] and info.st_mtime < query["since"]:
            continue
        is_folder = os.path.isdir(path)
        if is_folder != (query["kind"] == "folder") and query["kind"]:
            continue
        relative = os.path.relpath(os.path.dirname(path), home) if path.lower().startswith(home.lower()) else os.path.dirname(path)
        place = relative.split(os.sep)[0].lower()
        results.append({"path": path, "name": os.path.basename(path), "folder": relative, "size": 0 if is_folder else info.st_size,
                        "modified": int(info.st_mtime), "is_folder": is_folder,
                        "_rank": (by_name, not path.lower().endswith(CODE), place in BEST_FOLDERS, info.st_mtime)})
    if query["newest"]:
        results.sort(key=lambda r: r["modified"], reverse=True)
    else:
        results.sort(key=lambda r: r["_rank"], reverse=True)
    for result in results:
        result.pop("_rank")
    return results[:most]


def when_words(stamp: float, now: float = None) -> str:
    moment, today = dt.datetime.fromtimestamp(stamp), dt.datetime.fromtimestamp(now or time.time()).date()
    days = (today - moment.date()).days
    if days == 0:
        return "today " + moment.strftime("%I:%M %p").lstrip("0")
    if days == 1:
        return "yesterday"
    if days < 7:
        return moment.strftime("%A")
    return moment.strftime("%b %d, %Y").replace(" 0", " ")
