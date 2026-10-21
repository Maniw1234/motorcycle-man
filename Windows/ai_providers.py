# The AI behind Motorcycle Man, chosen by each user with their own key: Google Gemini (it has a free tier),
# Anthropic Claude, OpenAI (ChatGPT), or any service that speaks OpenAI's API: OpenRouter, Groq, Mistral,
# DeepSeek, xAI, or a model on the PC itself through Ollama or LM Studio. Motorcycle Man writes its requests
# the way Gemini takes them (it started with Gemini); this module turns them into each service's own request,
# and the answer back into Gemini's shape, so the rest of the app reads one kind of answer.
import json
import re
import urllib.error
import urllib.request
import uuid

PROVIDERS = {
    "gemini": {"label": "Google Gemini", "short": "Gemini", "key_name": "gemini_api_key",
               "key_url": "https://aistudio.google.com/apikey", "key_hint": "AIza… or AQ.…",
               "about": "Free with a Google account (Google may use what's sent on the free tier)."},
    "claude": {"label": "Anthropic Claude", "short": "Claude", "key_name": "claude_api_key",
               "key_url": "https://console.anthropic.com/settings/keys", "key_hint": "sk-ant-…",
               "default_model": "claude-opus-5-5", "about": "Paid by use on your Anthropic account."},
    "openai": {"label": "OpenAI (ChatGPT)", "short": "ChatGPT", "key_name": "openai_api_key",
               "key_url": "https://platform.openai.com/api-keys", "key_hint": "sk-…",
               "base_url": "https://api.openai.com/v1", "about": "Paid by use on your OpenAI account."},
    "compatible": {"label": "Another AI (OpenAI-compatible)", "short": "AI", "key_name": "compatible_api_key",
                   "key_url": "", "key_hint": "its key (none for Ollama or LM Studio)",
                   "about": "OpenRouter, Groq, Mistral, DeepSeek, xAI, or Ollama and LM Studio on this PC."},
}
# Picked from a ChatGPT key's models when none was chosen: quick ones that can see pictures first.
OPENAI_PREFERRED = ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o")
OPENAI_TRANSCRIBE_MODELS = ("gpt-4o-mini-transcribe", "whisper-1")
# Claude models that take fallbacks="default": a request one declines is run again on the model Anthropic
# recommends for it, instead of failing.
CLAUDE_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
CLAUDE_EFFORT_MODELS = re.compile(r"claude-(?:(?:opus|sonnet|haiku|fable|mythos)-5|opus-4-[678]|sonnet-4-6)")
JSON_NOTE = "Reply with only the JSON object, with no other text before or after it."
DEFAULT_MAX_TOKENS = 16000


class AiError(RuntimeError):
    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


def plain_parts(payload: dict):
    # (instructions, [("text", words) | ("image", type, base64) | ("audio", type, base64)]) from a Gemini request.
    system = " ".join(str(p.get("text", "")) for p in ((payload.get("system_instruction") or payload.get("systemInstruction")
                                                       or {}).get("parts") or []) if isinstance(p, dict)).strip()
    items = []
    for content in payload.get("contents") or []:
        for part in content.get("parts") or []:
            if "text" in part:
                items.append(("text", str(part["text"])))
            data = part.get("inline_data") or part.get("inlineData")
            if isinstance(data, dict):
                mime = str(data.get("mime_type") or data.get("mimeType") or "")
                items.append(("image" if mime.startswith("image/") else "audio", mime, str(data.get("data", ""))))
    return system, items


def wants_json(payload: dict) -> bool:
    return (payload.get("generationConfig") or {}).get("responseMimeType") == "application/json"


def gemini_shape(text: str, finish: str = "") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def ask_claude(key: str, model: str, payload: dict, timeout: float, effort: str = "low", client_factory=None) -> dict:
    import anthropic
    system, items = plain_parts(payload)
    content = []
    for item in items:
        if item[0] == "text":
            content.append({"type": "text", "text": item[1]})
        elif item[0] == "image":
            content.append({"type": "image", "source": {"type": "base64", "media_type": item[1], "data": item[2]}})
        else:
            raise AiError("Claude can't listen to recordings.")
    if wants_json(payload):
        system = (system + "\n\n" + JSON_NOTE).strip()
    request = {"model": model, "max_tokens": DEFAULT_MAX_TOKENS, "messages": [{"role": "user", "content": content}]}
    if system:
        request["system"] = system
    if effort and CLAUDE_EFFORT_MODELS.match(model):
        request["output_config"] = {"effort": effort}  # little thinking: Motorcycle Man is usually in a hurry
    client = (client_factory or anthropic.Anthropic)(api_key=key, timeout=timeout, max_retries=1)
    try:
        if model in CLAUDE_FALLBACK_MODELS:
            message = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
        else:
            message = client.messages.create(**request)
    except anthropic.AuthenticationError:
        raise AiError("Claude didn't accept the key.", 401) from None
    except anthropic.PermissionDeniedError:
        raise AiError(f"This Claude key may not use {model}.", 403) from None
    except anthropic.NotFoundError:
        raise AiError(f"Claude doesn't know the model {model}.", 404) from None
    except anthropic.RateLimitError:
        raise AiError("Claude is busy, or the key is over its limit. Try again in a minute.", 429) from None
    except anthropic.APITimeoutError:
        raise AiError("Claude took too long to answer.") from None
    except anthropic.APIStatusError as e:
        raise AiError(f"Claude answered HTTP {e.status_code}: {str(e.message)[:200]}", e.status_code) from None
    except anthropic.APIConnectionError:
        raise AiError("Can't reach Claude. Check the internet connection.") from None
    if message.stop_reason == "refusal":
        raise AiError("Claude declined this request.")
    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return gemini_shape(text, str(message.stop_reason or ""))


def openai_base(provider: str, base_url: str = "") -> str:
    return (base_url or PROVIDERS[provider].get("base_url", "")).strip().rstrip("/")


def _send(request: urllib.request.Request, timeout: float, who: str, opener=None) -> dict:
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as reply:
            return json.loads(reply.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read().decode("utf-8", "replace")).get("error") or {}
        except (ValueError, AttributeError):
            error = {}
        finally:
            e.close()
        detail = str(error.get("message", "") if isinstance(error, dict) else error)[:200]
        if e.code == 401:
            raise AiError(f"{who} didn't accept the key.", 401) from None
        if e.code == 429:
            raise AiError(f"{who} is busy, or the key is over its limit. Try again in a minute.", 429) from None
        if e.code == 404:
            raise AiError(f"{who} doesn't know that model or address. {detail}".strip(), 404) from None
        raise AiError(f"{who} answered HTTP {e.code}. {detail}".strip(), e.code) from None
    except (OSError, ValueError) as e:  # no connection, a timeout, or an answer that isn't JSON
        raise AiError(f"Can't reach {who}: {e}"[:200]) from None


def _headers(key: str, kind: str = "application/json") -> dict:
    headers = {"Content-Type": kind, "User-Agent": "MotorcycleMan"}
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers


def ask_openai(provider: str, key: str, model: str, base_url: str, payload: dict, timeout: float, opener=None) -> dict:
    # Chat completions, which OpenAI and the services copying its API all take.
    who = PROVIDERS[provider]["short"] if provider == "openai" else (model or "The AI")
    system, items = plain_parts(payload)
    content = []
    for item in items:
        if item[0] == "text":
            content.append({"type": "text", "text": item[1]})
        elif item[0] == "image":
            content.append({"type": "image_url", "image_url": {"url": f"data:{item[1]};base64,{item[2]}"}})
        else:
            raise AiError(f"{who} can't listen to recordings here.")
    if wants_json(payload):
        system = (system + "\n\n" + JSON_NOTE).strip()
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": model, "messages": messages}
    if provider == "openai":
        body["max_completion_tokens"] = DEFAULT_MAX_TOKENS
        if wants_json(payload):
            body["response_format"] = {"type": "json_object"}
        if re.match(r"(?:gpt-5|o\d)", model):
            body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = 8192  # not every service takes more, or OpenAI's newer names for these settings
    request = urllib.request.Request(openai_base(provider, base_url) + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"), headers=_headers(key), method="POST")
    data = _send(request, timeout, who, opener)
    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if isinstance(text, list):
        text = "".join(str(p.get("text", "")) for p in text if isinstance(p, dict))
    if message.get("refusal") and not text:
        raise AiError(f"{who} declined this request.")
    return gemini_shape(str(text), str(choice.get("finish_reason") or ""))


def transcribe_openai(key: str, audio: bytes, mime_type: str, timeout: float = 30, opener=None) -> str:
    # The words in a recording, through OpenAI's speech-to-text.
    extension = {"audio/wav": "wav", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
                 "audio/webm": "webm"}.get(mime_type, "wav")
    last = None
    for model in OPENAI_TRANSCRIBE_MODELS:
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.{extension}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n").encode("utf-8") + audio + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(PROVIDERS["openai"]["base_url"] + "/audio/transcriptions", data=body,
                                         headers=_headers(key, f"multipart/form-data; boundary={boundary}"), method="POST")
        try:
            return str(_send(request, timeout, "ChatGPT", opener).get("text", "")).strip()
        except AiError as e:
            last = e
            if e.status not in (400, 403, 404):  # another model only helps when this one isn't available
                break
    raise last


def list_models(provider: str, key: str, base_url: str = "", timeout: float = 15, opener=None, client_factory=None) -> list:
    # The models a key may use (which also shows the key works), newest names first where the service says.
    if provider == "claude":
        import anthropic
        client = (client_factory or anthropic.Anthropic)(api_key=key, timeout=timeout, max_retries=1)
        try:
            return [m.id for m in client.models.list(limit=100)]
        except anthropic.AuthenticationError:
            raise AiError("Claude didn't accept the key.", 401) from None
        except anthropic.APIStatusError as e:
            raise AiError(f"Claude answered HTTP {e.status_code}: {str(e.message)[:200]}", e.status_code) from None
        except anthropic.APIConnectionError:
            raise AiError("Can't reach Claude. Check the internet connection.") from None
    if provider == "gemini":
        request = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                         headers={"x-goog-api-key": key, "User-Agent": "MotorcycleMan"})
        data = _send(request, timeout, "Gemini", opener)
        return [str(m.get("name", "")).removeprefix("models/") for m in data.get("models") or []
                if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    who = PROVIDERS[provider]["short"]
    data = _send(urllib.request.Request(openai_base(provider, base_url) + "/models", headers=_headers(key)), timeout, who, opener)
    return sorted((str(m.get("id", "")) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")),
                  key=lambda name: (name not in OPENAI_PREFERRED, OPENAI_PREFERRED.index(name) if name in OPENAI_PREFERRED else 0, name))


def suggested_model(provider: str, models: list) -> str:
    # A sensible first choice from a key's models.
    if provider == "claude":
        default = PROVIDERS["claude"]["default_model"]
        return default if default in models or not models else models[0]
    if provider == "openai":
        return next((m for m in OPENAI_PREFERRED if m in models), next((m for m in models if m.startswith("gpt-")), ""))
    return models[0] if models else ""
