"""Project-relative paths and explicit Gemini configuration."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://docs.langchain.com/oss/python/langchain/overview"


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str = "gemini-3.8-flash"
    embedding_model: str = "models/gemini-embedding-001"
    assets_dir: Path = ROOT / "assets"
    index_dir: Path = ROOT / "faiss_index"

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("Set GEMINI_API_KEY in the project .env or environment.")
        model = os.getenv("GEMINI_MODEL", cls.model)
        if "live" in model.lower():
            raise ValueError("GEMINI_MODEL must be a standard generation model.")
        return cls(api_key=key, model=model,
                   embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", cls.embedding_model))
