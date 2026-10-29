"""Session-local hang-up authorization and idle deadlines."""
import re
import time


def words(text):
    return " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())


def direct_end_request(text):
    value = words(text)
    command = (
        r"(?:(?:yes|okay|ok) )?(?:(?:please|can you|could you|would you) )?"
        r"(?:hang ?up(?: (?:the|this) call)?|(?:end|disconnect|close|terminate|stop) "
        r"(?:(?:the|this|our|my) )?(?:call|conversation|session)|disconnect|stop listening|quit|exit)"
        r"(?: (?:now|please|thank you|thanks))?")
    if re.fullmatch(command, value):
        return True
    # Real speech contains lead-ins, explanations and a closing thank-you.
    # Accept an explicit call-ending clause, never a quoted/negated/conditional one.
    for match in re.finditer(r"\b(?:hang ?up(?: (?:the|this) call)?|(?:end|disconnect|close|terminate|stop) "
                             r"(?:(?:the|this|our|my) )?(?:call|conversation|session)|stop listening)\b", value):
        before = value[:match.start()]
        if re.search(r"\b(?:don't|dont|do not|never|not to|cannot|can't|shouldn't|wouldn't)\b(?: \w+){0,3}\s*$", before):
            continue
        if re.search(r"\b(?:if|unless|whether|what|why|how|when|said|says|say|means?|example|quoted?)\b", before):
            continue
        if re.search(r"\b(?:not ready to|not asking (?:you )?to)\s*$", before):
            continue
        return True
    return False


def end_confirmation_question(text):
    """Reuse a generated question only when it asks permission to end the call."""
    return text.rstrip().endswith("?") and bool(re.search(
        r"\b(?:would you like|do you want|shall (?:i|we)|should (?:i|we)|may i|can i|"
        r"is it (?:okay|ok) (?:if|to)|are you (?:ready|happy) (?:for|to))\b.{0,45}"
        r"\b(?:end|close|disconnect|finish|wrap up|hang up)\b", words(text)))


class CallControl:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.pending = False
        self.asked_at = float("inf")
        self.last_activity = clock()

    def touch(self):
        self.last_activity = self.clock()

    def decide(self, text, *, started_at):
        value = words(text)
        if direct_end_request(text):
            return "end"
        if self.pending:
            self.pending = False
            if re.fullmatch(r"(?:(?:yes|yeah|yep|sure|okay|ok)(?: (?:yes|yeah|yep|of course|please|go ahead|absolutely|sure|thank you|thanks))*|"
                            r"of course|absolutely|go ahead|please do|confirm|confirmed)", value):
                return "end" if started_at >= self.asked_at else "ask"
            if re.fullmatch(r"(?:no|no thanks|no thank you|not yet|not now|keep (?:it|the call) open|stay on)", value):
                return "stay"
            # No, a new question, or an ambiguous answer never authorizes hang-up.
            return "continue"
        if re.fullmatch(r"(?:that(?:'s| is) all(?: thank you)?|nothing else(?: thanks)?|"
                        r"i(?:'m| am) done|bye|goodbye|no (?:that(?:'s| is) all|more questions))", value):
            return "ask"
        return "continue"

    def asked(self):
        self.pending = True
        self.asked_at = self.clock()
