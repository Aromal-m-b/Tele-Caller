"""Local scope enforcement before retrieval, models and account-tool selection.

No model calls are used to decide obvious refusals. This is an allowlisted support
boundary, with explicit abuse checks taking precedence over domain keywords.
"""
from dataclasses import dataclass
from collections import deque
import re
import threading
import time
import unicodedata

REFUSAL = "Sorry, that's outside what I can help with here. I'm here for Sun Direct support."
CLARIFICATION = "I didn't quite catch what you need. Could you say a little more?"


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return " ".join(text.split())


def is_audio_check(text):
    # Match the complete utterance: a greeting must never bypass abuse checks
    # for additional instructions appended to it.
    value = re.sub(r"[.!?,]", " ", normalize(text))
    value = " ".join(value.split())
    return bool(re.fullmatch(
        r"(?:(?:hello|hi|hey|okay|ok) )?(?:can you hear me(?: now)?|"
        r"can you get on|are you (?:there|listening)|is (?:my|the) (?:mic|microphone|audio) working|"
        r"can you hear what i(?:'m| am) saying)", value))


def is_identity_question(text):
    """Recognize identity/addressing intent inside conversational ASR phrasing.

    Called only after the guard's abuse checks, so a name question cannot authorize
    additional programming, instruction overrides or database extraction.
    """
    return bool(re.search(
        r"\b(?:your name|who (?:are|is) you|who am i (?:talking|speaking) to|"
        r"(?:what|how) (?:should|can|do) i (?:call|address) you|"
        r"(?:what|which) name (?:do you|should i)|are you maya)\b", normalize(text)))


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    reason: str
    response: str = ""


_PROGRAMMING = re.compile(
    r"\bc\+\+(?!\w)|\bc#(?!\w)|\b(python|pyth0n|javascript|typescript|java|csharp|html|css|bash|powershell|sql|"
    r"ruby|php|perl|rust|swift|kotlin|fortran|matlab|assembly|dotnet|implement|"
    r"programming|programmer|coding|algorithm|leetcode|hello[ -]+world|source code|"
    r"shell script|terminal command|mongo(?:db)? quer(?:y|ies))\b|"
    r"\b(?:write|generate|create|produce|debug|execute|run)\b.{0,70}\b(?:program|script|function|code)\b", re.I)
_OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|override|bypass|disable|forget)\b.{0,65}\b(?:instructions?|rules?|polic(?:y|ies)|"
    r"guardrails?|restrictions?|system prompt|scope)\b|"
    r"\b(?:developer mode|jailbreak|unrestricted assistant|system prompt|hidden prompt|api[ _-]?keys?|"
    r"connection string|mongodb_uri|tool schemas?)\b|"
    r"\b(?:pretend|roleplay|act as)\b.{0,60}\b(?:developer|programmer|admin|unrestricted|general.purpose)\b|"
    r"(?:\[/?system\]|<\|(?:system|im_start)\|>)", re.I)
_BULK_DATA = re.compile(
    r"\b(?:dump|export|list|show|reveal|give|print|retrieve)\b.{0,60}\b(?:all customers|all users|"
    r"every customer|entire database|database tables|database collections|credentials|passwords)\b|"
    r"\b(?:drop|delete|update|insert|truncate)\s+(?:table|collection|database)\b|"
    r"\b(?:db\.[\w.]+\(|eval\s*\(|exec\s*\()", re.I)
_OTHER_TASK = re.compile(
    r"\b(?:write|compose|generate|tell|narrate|draft)\b.{0,60}\b(?:essay|poem|novel|story|song|resume|homework|joke|screenplay)\b|"
    r"\b(?:solve|calculate)\b.{0,60}\b(?:equation|integral|algebra|homework)\b|"
    r"\b(?:sun(?!\s*direct)|stars?|universe|earth)\b.{0,35}\b(?:formed|formation|created|born)\b|"
    r"\b(?:build|design|create)\b.{0,50}\b(?:website|web app|application|chatbot)\b|"
    r"\b(?:base64|rot13|hex.encoded|encrypted instructions)\b", re.I)
_DOMAIN = re.compile(
    r"\b(?:sun\s*direct|sundirect|dth|dish|set.top.box|stb|receiver|remote|television|tv|"
    r"recharg\w*|reche?rg\w*|subscription|subscrib\w*|bouquet|pack(?:age)?s?|plans?|"
    r"channels?|payment|paid|billing|bill|balance|refund|validity|expir\w*|renew\w*|"
    r"tickets?|issues?|verify|verification|distributor|dealership|dealer|partnership|new customer|business enquiry|"
    r"complaints?|orders?|deliver(?:y|ies)|tracking|courier|relocation|relocating|cancelling|cancellation|dish|remote|wire|adapter|connector|"
    r"account|profile|registered mobile|smart.?card|cdsn|cr.?id|customer.?id|"
    r"signal|cable|picture|screen|error|complaint|technician|installation|"
    r"movies|entertainment|sports|shopping|ott|sun nxt|sun direct go|login|otp|"
    r"(?:reset|recover|forgot|change).{0,20}password|password reset)\b", re.I)
_LANGUAGE = re.compile(r"\b(?:malayalam|tamil|telugu|kannada|hindi|bengali|marathi|odia|english)\b", re.I)
_FOLLOWUP = re.compile(
    r"^(?:yes|no|okay|ok|thanks|thank you|confirm|confirmed|go ahead|not now|cancel|"
    r"continue with it|the previous one|the same one|that one|this one|"
    r"what about|how much|how long|which one|what is included|what does it include|"
    r"i prefer|i want|i need|exclude|avoid|instead|actually|correction|remember|"
    r"which language|what kind of|what did i|what was my|what is my current preference)\b", re.I)


class SupportScopeGuard:
    def __init__(self, *, max_requests=30, window_seconds=60, clock=time.monotonic):
        self.max_requests, self.window_seconds, self.clock = max_requests, window_seconds, clock
        self._calls = deque()
        self._lock = threading.Lock()

    def reserve_backend_request(self):
        """Per-session resource cap; local greetings/refusals need no backend slot."""
        with self._lock:
            now = self.clock()
            while self._calls and self._calls[0] <= now - self.window_seconds:
                self._calls.popleft()
            if len(self._calls) >= self.max_requests:
                return ScopeDecision(False, "rate_limited", "Please pause a moment before your next support request.")
            self._calls.append(now)
            return ScopeDecision(True, "backend_budget_available")

    def evaluate(self, text, *, context=""):
        if len(text) > 4000:
            return ScopeDecision(False, "request_too_long", "Please keep your request to a short Sun Direct service question.")
        value = normalize(text)
        if len(value) > 4000:
            return ScopeDecision(False, "request_too_long", "Please keep your request to a short Sun Direct service question.")
        # Collapsed forms catch obvious whitespace/zero-width obfuscation without
        # rewriting the customer request that eventually reaches the assistant.
        collapsed = re.sub(r"[^a-z0-9]", "", value)
        obfuscated = any(term in collapsed for term in ("helloworld", "systemprompt", "ignorepreviousinstructions", "python", "javascript"))
        for reason, pattern in [("instruction_override", _OVERRIDE), ("data_or_query_abuse", _BULK_DATA),
                                ("programming_request", _PROGRAMMING), ("unrelated_task", _OTHER_TASK)]:
            if pattern.search(value):
                return ScopeDecision(False, reason, REFUSAL)
        if obfuscated:
            return ScopeDecision(False, "obfuscated_disallowed_request", REFUSAL)
        if is_audio_check(value):
            return ScopeDecision(True, "audio_check")
        if is_identity_question(value):
            return ScopeDecision(True, "identity_question")
        if _DOMAIN.search(value):
            return ScopeDecision(True, "support_topic")
        if re.fullmatch(r"(?:hi|hello|hey|who are you|what is your name|what's your name|thanks|thank you)[.!?, ]*", value):
            return ScopeDecision(True, "greeting")
        has_support_context = bool(_DOMAIN.search(normalize(context)) or _LANGUAGE.search(normalize(context)))
        if has_support_context and (_FOLLOWUP.search(value) or _LANGUAGE.search(value)):
            return ScopeDecision(True, "support_followup")
        if has_support_context and re.match(r"^(?:under|below|around|up to|less than)\s+(?:inr\s*|rs\.?\s*|₹\s*)?\d", value):
            return ScopeDecision(True, "budget_followup")
        if _LANGUAGE.fullmatch(value.strip(".!?, ")):
            return ScopeDecision(False, "missing_support_context", CLARIFICATION)
        if len(value.split()) <= 4:
            return ScopeDecision(False, "incomplete_or_ambiguous", CLARIFICATION)
        return ScopeDecision(False, "outside_support_scope", REFUSAL)

    def screen_answer(self, answer):
        value = normalize(answer)
        if (re.search(r"```(?:python|py|javascript|js|typescript|java|c|cpp|csharp|ruby|php|perl|rust|swift|kotlin|bash|sql|sh|powershell|html|css)\b", answer, re.I)
                or re.search(r"\b(?:print|eval|exec)\s*\(|\bdef\s+\w+\s*\(|\bimport\s+(?:os|sys|subprocess)\b|\bconsole\.log\s*\(|\bSystem\.out\.print", answer)
                or re.search(r"\bi(?: can| am able to|['’]m able to)\b.{0,60}\b(?:write|generate|execute|run|provide|produce|create|debug)\b.{0,50}\b(?:code|scripts?|programs?)\b", value)
                or re.search(r"\bi (?:have |just |successfully )?(?:charged|renewed|processed your payment|updated your subscription)\b", value)):
            return REFUSAL
        return answer
