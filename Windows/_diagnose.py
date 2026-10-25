from pathlib import Path
import ast, re, sys

p = Path(sys.argv[1]) / "motorcycle_man.py"
s = p.read_text(encoding="utf-8-sig")
tree = ast.parse(s)
imports = {
    alias.name
    for node in ast.walk(tree)
    if isinstance(node, (ast.Import, ast.ImportFrom))
    for alias in node.names
}
print("Python syntax: OK")
print("Source size:", len(s), "characters")
print("Missing webbrowser import:", "webbrowser" not in imports)
print("Missing urllib.request import:", "urllib.request" not in imports)
print("Unsafe empty-speech fallback:", 'spokenText.trim() || "Open ChatGPT"' in s)
print("LAN binding default:", bool(re.search(r'SERVER_HOST\s*=\s*os\.environ\.get\("MOTORCYCLE_MAN_HOST",\s*"0\.0\.0\.0"\)', s)))
print("Phone endpoint session token required:", "phone_request_is_authorized(parsed.query)" in s)
print("Screenshot-specific coordinate bounds:", "action_plan_schema_for_screenshot" in s)
print("Structured per-action plan schema:", '"anyOf"' in s and '"additionalProperties": False' in s)
print("PowerShell available as an app action:", '"powershell": [' in s)
