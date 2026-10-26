"""Accumulate stable STT segments into complete spoken turns."""


class TranscriptBuffer:
    def __init__(self):
        self.parts = []

    def consume(self, event):
        if event.get("type") == "Results":
            alternatives = event.get("channel", {}).get("alternatives", [])
            text = alternatives[0].get("transcript", "") if alternatives else ""
            if event.get("is_final") and text.strip():
                self.parts.append(text.strip())
            if not event.get("speech_final"):
                return None
        elif event.get("type") != "UtteranceEnd":
            return None
        text = " ".join(self.parts)
        self.parts.clear()
        return text or None
