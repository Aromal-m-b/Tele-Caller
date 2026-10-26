"""Session-local hang-up authorization and idle deadlines."""
import re
import time


def words(text):
    return " ".join(re.sub(r"[^\w\s']", " ", text.casefold()).split())


def direct_end_request(text):
    return bool(re.fullmatch(
        r"(?:(?:yes|okay|ok) )?(?:(?:please|can you|could you|would you) )?"
        r"(?:hang ?up(?: (?:the|this) call)?|(?:end|disconnect|close|terminate|stop) "
        r"(?:(?:the|this|our|my) )?(?:call|conversation|session)|disconnect|stop listening|quit|exit)"
        r"(?: (?:now|please|thank you|thanks))?", words(text)))


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
            if re.fullmatch(r"(?:yes|yes please|yes go ahead|go ahead|sure|okay|ok|please do|confirm|confirmed)", value):
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
