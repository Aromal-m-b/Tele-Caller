"""Local, session-only conversation memory. No network or disk writes."""
from collections import Counter
from dataclasses import dataclass
import json
import math
import re
import zlib

_STOP = set("a an and are as at be by can did do for from how i in is it me my of on or that the this to was we what which with you your earlier before said tell".split())
_PREFERENCE = re.compile(r"\b(prefer|want|need|exclude|avoid|budget|instead|actually|correction|remember|only|don't|not)\b", re.I)


def terms(text):
    return {word.removesuffix("s") for word in re.findall(r"[\w]+", text.lower())
            if len(word) > 2 and word not in _STOP}


def compact(text, limit):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 3)].rsplit(" ", 1)[0] + "..."


@dataclass(frozen=True)
class MemoryNote:
    number: int
    compressed: bytes
    keywords: frozenset
    customer_anchor: bool

    def messages(self):
        return json.loads(zlib.decompress(self.compressed).decode("utf-8"))


class ConversationMemory:
    def __init__(self, *, recent_exchanges=6):
        if recent_exchanges < 1:
            raise ValueError("recent_exchanges must be positive")
        self.recent_limit = recent_exchanges * 2
        self.recent = []
        self.notes = []

    def append(self, user, assistant):
        self.recent.extend([user, assistant])
        while len(self.recent) > self.recent_limit:
            older = self.recent[:2]
            self.recent = self.recent[2:]
            record = [{"role": message.type, "text": message.content} for message in older]
            text = " ".join(str(message.content) for message in older)
            self.notes.append(MemoryNote(
                len(self.notes) + 1,
                zlib.compress(json.dumps(record, ensure_ascii=False).encode("utf-8")),
                frozenset(terms(text)), bool(_PREFERENCE.search(str(older[0].content)))))

    def context(self, query, *, limit_chars=5000):
        """Extract relevant notes plus preference anchors within a fixed prompt budget.

        The archive itself stays lossless. Excerpts are clearly marked, and newer
        notes appear later so explicit corrections can supersede older preferences.
        """
        if not self.notes or limit_chars <= 0:
            return ""
        query_terms = terms(query)
        counts = Counter(term for note in self.notes for term in note.keywords)
        def score(note):
            return sum(math.log(1 + len(self.notes) / counts[t])
                       for t in query_terms & note.keywords)
        ranked = sorted(self.notes, key=lambda n: (score(n), n.number), reverse=True)
        anchors = [note for note in self.notes if note.customer_anchor]
        candidates = ranked[:2] + self.notes[-2:] + anchors[:2] + anchors[-2:]
        selected = {}
        # Preserve exact query matches first, then initial and latest preference anchors.
        for note in candidates:
            if len(selected) >= 8:
                break
            selected[note.number] = note
        per_note = max(1, min(700, limit_chars // max(1, len(selected)) - 90))
        sections = []
        for number, note in sorted(selected.items()):
            messages = note.messages()
            customer = compact(str(messages[0]["text"]), max(1, per_note * 2 // 3))
            assistant = compact(str(messages[1]["text"]), max(1, per_note // 3))
            sections.append(f"Older exchange {number}:\nCustomer: {customer}\n"
                            f"Assistant previously said (not verified evidence): {assistant}")
        return "\n\n".join(sections)[:limit_chars]

    @property
    def summary(self):
        return self.context("", limit_chars=2400)

    def clear(self):
        self.recent.clear()
        self.notes.clear()
