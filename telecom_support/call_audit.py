"""Private local call transcript and provider-usage estimate, never an invoice."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import threading
import time
import uuid
import os
import math

from langchain_core.callbacks import BaseCallbackHandler


class CallAudit(BaseCallbackHandler):
    def __init__(self, folder, *, caller="", model="", clock=time.monotonic):
        self.call_id = uuid.uuid4().hex
        self.clock, self.started = clock, clock()
        self.model, self.identity = model, "unverified"
        newer_flash = model.startswith("gemini-3.8-flash")
        regular_2027 = datetime.now(timezone.utc).date().isoformat() >= "2027-01-01"
        input_default = ("1.5" if regular_2027 else ".75") if newer_flash else ".5"
        output_default = ("7.5" if regular_2027 else "3.75") if newer_flash else "3"
        self.rates = {key: float(os.getenv(env, default)) for key, env, default in [
            ("input_per_million", "COST_GEMINI_INPUT_PER_MILLION", input_default),
            ("output_per_million", "COST_GEMINI_OUTPUT_PER_MILLION", output_default),
            ("stt_per_minute", "COST_STT_PER_MINUTE", ".0077"),
            ("tts_per_1000_characters", "COST_TTS_PER_1000_CHARACTERS", ".03")]}
        if any(not math.isfinite(v) or v < 0 for v in self.rates.values()):
            raise ValueError("Cost-estimate rates must be finite, nonnegative USD amounts.")
        self.lock, self.secrets = threading.RLock(), []
        self.input_tokens = self.output_tokens = self.requests = self.missing_usage = 0
        self.failed_requests = self.reasoning_tokens = 0
        self.stt_bytes = self.tts_characters = 0
        self.closed = False
        self.stt_start = self.stt_end = None
        Path(folder).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = Path(folder) / f"{stamp}_{self.call_id}.txt"
        self.path.write_text(f"CALL {self.call_id}\nStarted UTC: {stamp}\nSimulated caller: {caller}\nModel: {model}\n\nTRANSCRIPT\n", encoding="utf-8")

    def redact(self, value):
        if value:
            with self.lock:
                self.secrets.append(str(value))

    def event(self, text):
        with self.lock:
            safe = str(text)
            for secret in self.secrets:
                safe = safe.replace(secret, "[verification value redacted]")
            safe = re.sub(r"(?i)((?:smart.?card|password|otp|pin)\s*[:=]\s*)\S+", r"\1[redacted]", safe)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(f"[{self.clock() - self.started:.3f}s] {safe}\n")

    def write(self, text):
        print(text)
        if text.startswith(("You: ", "A: ")) or "Goodbye" in text or "stopped" in text:
            self.event(text)

    def on_llm_end(self, response, **kwargs):
        with self.lock:
            for group in response.generations:
                for generation in group:
                    usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                    self.requests += 1
                    if usage:
                        self.input_tokens += usage.get("input_tokens", 0)
                        self.output_tokens += usage.get("output_tokens", 0)
                        self.reasoning_tokens += usage.get("output_token_details", {}).get("reasoning", 0)
                    else:
                        self.missing_usage += 1

    def on_llm_error(self, error, **kwargs):
        with self.lock:
            self.failed_requests += 1
            self.event("Model request failed (" + type(error).__name__ + "); provider-billed usage may be unavailable.")

    def audio_sent(self, size):
        with self.lock: self.stt_bytes += size

    def stt_connected(self):
        with self.lock:
            self.stt_start, self.stt_end = self.clock(), None

    def stt_disconnected(self):
        with self.lock:
            if self.stt_start is not None: self.stt_end = self.clock()

    def synthesized(self, count):
        with self.lock: self.tts_characters += count

    def close(self):
        with self.lock:
            if self.closed: return
            self.closed = True
            duration = self.clock() - self.started
            stt_duration = ((self.stt_end if self.stt_end is not None else self.clock()) - self.stt_start
                            if self.stt_start is not None else 0)
            if getattr(self, "backend", None) == "deepgram-agent":
                rate = getattr(self, "agent_rate_per_minute", None)
                summary = dict(call_id=self.call_id, backend="deepgram-agent", model=self.model,
                    duration_seconds=round(duration, 3), agent_connected_seconds=round(stt_duration, 3),
                    agent_rate_per_minute_usd=rate,
                    estimated_total_usd=stt_duration / 60 * rate if rate is not None else None,
                    input_tokens=None, output_tokens=None, total_tokens=None,
                    cost_is_estimate=True, complete_provider_invoice=False,
                    cost_basis="Managed agent billed on connection time. Hosted LLM tokens unavailable, not zero. "
                        "No separate STT/TTS/model costs added. Query embedding usage/cost is unavailable and excluded. "
                        "Configured rate must match the selected managed tier; not a provider invoice.")
                self.event("\nUSAGE AND COST\n" + json.dumps(summary, indent=2))
                return
            summary = dict(call_id=self.call_id, customer_id=self.identity, duration_seconds=round(duration, 3),
                gemini_requests=self.requests, input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                total_tokens=self.input_tokens + self.output_tokens, requests_without_usage_metadata=self.missing_usage,
                failed_model_requests=self.failed_requests, reasoning_tokens_included_in_output=self.reasoning_tokens,
                stt_audio_seconds=self.stt_bytes / 32000, stt_connected_seconds=round(stt_duration, 3), tts_accepted_characters=self.tts_characters,
                gemini_estimate_usd=self.input_tokens * self.rates["input_per_million"] / 1e6 + self.output_tokens * self.rates["output_per_million"] / 1e6,
                stt_conservative_estimate_usd=stt_duration / 60 * self.rates["stt_per_minute"],
                tts_estimate_usd=self.tts_characters / 1000 * self.rates["tts_per_1000_characters"],
                configured_rates_usd=self.rates, cost_is_estimate=True, complete_provider_invoice=False)
            summary["estimated_total_usd"] = sum(summary[k] for k in ("gemini_estimate_usd", "stt_conservative_estimate_usd", "tts_estimate_usd"))
            summary["cost_basis"] = ("Uses the configured rates shown above; model-aware defaults are a published pricing snapshot for standard text generation, Nova-3 and Aura-2. "
                "Estimate, not provider invoice. Embedding/OCR tokens and costs are unavailable and excluded. "
                "Cancelled accepted TTS requests may still be billed. Missing model usage makes totals incomplete.")
            self.event("\nUSAGE AND COST\n" + json.dumps(summary, indent=2))
