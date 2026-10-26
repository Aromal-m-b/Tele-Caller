import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    gemini_key: str = field(repr=False)
    deepgram_key: str = field(repr=False)
    model: str = "gemini-2.5-flash"
    stt_model: str = "nova-3"
    tts_model: str = "aura-2-thalia-en"
    embedding_model: str = "models/gemini-embedding-001"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        missing = [name for name in ("GEMINI_API_KEY", "DEEPGRAM") if not os.getenv(name)]
        if missing:
            raise ValueError("Missing environment variables: " + ", ".join(missing))
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if "live" in model.lower():
            raise ValueError("GEMINI_MODEL must be a standard text generation model.")
        return cls(os.environ["GEMINI_API_KEY"], os.environ["DEEPGRAM"], model,
                   os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
                   os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
                   os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001"))
