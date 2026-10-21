# "Alexa, ask Motorcycle Man to open Gmail": the user's own private Alexa skill (free, in Amazon's developer console)
# sends what was said to the PC's fixed link, at an address with a long secret in it, and Alexa says the answer.
# A request counts only at that secret address, for the user's own skill (its ID), and from the Amazon account that
# said the link code; Python here can't check Amazon's signatures (no crypto), so the secret address stands in for
# them, as the link's secret does for the phone. Anyone who can talk to that Echo can use it.
import datetime as dt, logging, re, threading, time

LOG = logging.getLogger("motorcycle_man")
CLOCK_SLACK = 150  # seconds a request's timestamp may be off, as Amazon allows
ANSWER_WITHIN = 6.0  # Alexa waits 8 seconds; a longer task is told "on it" and carries on
DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3", "four": "4",
               "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
VOICE_MODEL = {"interactionModel": {"languageModel": {
    "invocationName": "motorcycle man",
    "intents": [
        {"name": "CommandIntent", "slots": [{"name": "command", "type": "AMAZON.SearchQuery"}],
         "samples": ["to {command}", "please {command}", "and {command}", "if you can {command}", "my pc to {command}",
                     "link {command}", "code {command}"]},
        {"name": "AMAZON.StopIntent", "samples": []}, {"name": "AMAZON.CancelIntent", "samples": []},
        {"name": "AMAZON.HelpIntent", "samples": []}, {"name": "AMAZON.FallbackIntent", "samples": []},
        {"name": "AMAZON.NavigateHomeIntent", "samples": []}],
    "types": []}}}


def spoken_digits(text: str) -> str:
    # "four eight two 9 1 3" -> "482913".
    return "".join(DIGIT_WORDS.get(word, word if word.isdigit() else "") for word in re.findall(r"[a-z]+|\d+", str(text).lower()))


def speech(text: str, end: bool = True) -> dict:
    answer = {"version": "1.0", "response": {"outputSpeech": {"type": "PlainText", "text": str(text)[:6000]}, "shouldEndSession": end}}
    if not end:
        answer["response"]["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": "What should I do on your PC?"}}
    return answer


class AlexaSkill:
    def __init__(self, skill_id: str, link, spoken=lambda text: text):
        self.skill_id, self.link, self.spoken = skill_id.strip(), link, spoken
        link.transport = self

    def start(self):
        pass  # Alexa sends the requests

    def stop(self):
        pass

    def send(self, chat, text: str):
        pass  # Alexa can only answer what it was asked (alerts go to the other ways in)

    def answer(self, request_body: dict):
        # The answer for Alexa, or None when the request isn't for this skill or is out of date.
        system = (request_body.get("context") or {}).get("System") or {}
        session = request_body.get("session") or {}
        app_id = (system.get("application") or session.get("application") or {}).get("applicationId", "")
        user = (system.get("user") or session.get("user") or {}).get("userId", "")
        request = request_body.get("request") or {}
        if not self.skill_id or app_id != self.skill_id:
            return None
        try:
            sent = dt.datetime.fromisoformat(str(request.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
        if abs(time.time() - sent) > CLOCK_SLACK:
            return None
        kind = request.get("type")
        if kind == "LaunchRequest":
            return speech("Motorcycle Man here. What should I do on your PC?", end=False)
        if kind != "IntentRequest":
            return {"version": "1.0", "response": {}}
        intent = (request.get("intent") or {}).get("name", "")
        if intent in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
            return speech("Okay.")
        if intent in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent"):
            return speech("Say, ask Motorcycle Man to, and what to do on your PC. Like: ask Motorcycle Man to open Gmail.", end=False)
        command = str((((request.get("intent") or {}).get("slots") or {}).get("command") or {}).get("value") or "").strip()
        if user != self.link.owner:
            return self._link(user, command)
        if not command:
            return speech("What should I do on your PC?", end=False)
        return speech(self._run(command))

    def _link(self, user: str, command: str) -> dict:
        code = self.link.code
        if code and time.monotonic() < self.link.code_until and spoken_digits(command)[-6:] == code:
            self.link.owner, self.link.code = user, ""
            self.link.helper.paired(user)
            self.link.helper.changed("paired", "Alexa")
            return speech("You're linked! Now just ask Motorcycle Man to do anything on your PC.")
        return speech("This Motorcycle Man isn't linked to you yet. Say, ask Motorcycle Man to link, and the code on the PC.")

    def _run(self, command: str) -> str:
        done, reply = threading.Event(), {}

        def go():
            try:
                reply["text"] = self.link.helper.run(command)
            except Exception as exc:
                reply["text"] = f"That failed: {str(exc)[:200]}"
            done.set()

        threading.Thread(target=go, daemon=True).start()
        if not done.wait(ANSWER_WITHIN):
            return "On it. I'm working on it on your PC."
        return self.spoken(reply.get("text") or "Done.") or "Done."
