"""Sentence boundaries and a cancellable bridge from sync generation to speech."""
import asyncio
import re
import threading
import json
from concurrent.futures import TimeoutError


class StreamCancelled(Exception):
    """A caller interrupted a public answer; never execute a follow-on action."""


def conversation_tokens(tokens, raw_parts, cancelled=lambda: False):
    """Decode only an intent-first JSON text value; control intentions stay buffered."""
    previous = ""
    try:
        for token in tokens:
            if cancelled():
                raise StreamCancelled()
            raw_parts.append(token)
            raw = "".join(raw_parts).strip().removeprefix("```json").removeprefix("```").lstrip()
            if len(raw) > 8000:
                raise ValueError("Conversational response too long")
            header = re.match(r'^\{\s*"intent"\s*:\s*"(?:reply|decline|clarify)"\s*,\s*"text"\s*:\s*"', raw)
            if not header:
                continue
            encoded, escaped = "", False
            for char in raw[header.end():]:
                if char == '"' and not escaped:
                    break
                encoded += char
                escaped = char == "\\" and not escaped
            decoded = None
            # A token may end halfway through an escape (including \uXXXX).
            for trim in range(min(6, len(encoded)) + 1):
                candidate = encoded[:-trim] if trim else encoded
                try:
                    decoded = json.loads('"' + candidate + '"')
                    break
                except json.JSONDecodeError:
                    pass
            if decoded is None or not decoded.startswith(previous):
                continue
            if len(decoded.split()) > 60:
                raise ValueError("Conversational response too long")
            if decoded != previous:
                yield decoded[len(previous):]
                previous = decoded
        # Validate the complete envelope before releasing an unfinished last sentence.
        raw = "".join(raw_parts).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        if set(data) != {"intent", "text"} or data["intent"] not in {"reply", "decline", "clarify", "repeat", "confirm_end"}:
            raise ValueError("Invalid conversation envelope")
    finally:
        close = getattr(tokens, "close", None)
        if callable(close): close()


def sentences(buffer, final=False):
    """Hold unfinished sentences (including decimal prices) until more tokens arrive."""
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\u201c])', buffer)
    ready = parts if final else parts[:-1]
    return [p.strip() for p in ready if p.strip()], "" if final else parts[-1]


def stream_sentences(tokens, sink, screen, format_text, partial):
    """Close the provider iterator on scope rejection or caller interruption."""
    answer, pending = "", ""
    def emit(text):
        clean = format_text(text)
        screened = screen(clean)
        if screened != clean:
            raise ValueError("Unsafe streamed answer")
        sink(screened)
    try:
        for token in tokens:
            if getattr(sink, "cancelled", lambda: False)():
                raise StreamCancelled()
            answer += token
            partial(answer)
            pending += token
            if screen(answer) != answer:
                raise ValueError("Unsafe streamed answer")
            ready, pending = sentences(pending)
            for sentence in ready:
                emit(sentence)
        ready, _ = sentences(pending, final=True)
        for sentence in ready:
            emit(sentence)
        return answer
    finally:
        close = getattr(tokens, "close", None)
        if callable(close): close()


async def generate_and_speak(application, query, speaker):
    queue = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()
    stopped = threading.Event()
    emitted = False

    def publish(text):
        if stopped.is_set():
            raise StreamCancelled()
        from .latency import mark
        mark("first_sentence")
        future = asyncio.run_coroutine_threadsafe(queue.put(text), loop)
        while not stopped.is_set():
            try:
                future.result(timeout=.1)
                return
            except TimeoutError:
                continue
        future.cancel()
        raise StreamCancelled()

    publish.cancelled = stopped.is_set

    async def consume():
        nonlocal emitted
        try:
            while True:
                text = await queue.get()
                if text is None:
                    return
                if stopped.is_set():
                    continue
                emitted = True
                audit = getattr(application, "audit", None)
                if audit:
                    audit.event("A [streamed sentence]: " + text)
                await speaker.speak(text)
                if getattr(speaker, "interrupted", False):
                    stopped.set()
        except BaseException:
            stopped.set()
            raise

    consumer = asyncio.create_task(consume())
    worker = asyncio.create_task(asyncio.to_thread(application.generate_stream, query, publish))
    try:
        try:
            answer = await asyncio.shield(worker)
        except StreamCancelled:
            answer = ""
        finish = asyncio.create_task(queue.put(None))
        try:
            await asyncio.wait({finish, consumer}, return_when=asyncio.FIRST_COMPLETED)
            if consumer.done():
                await consumer
            await finish
        finally:
            finish.cancel()
            await asyncio.gather(finish, return_exceptions=True)
        await consumer
        return answer, emitted
    finally:
        stopped.set()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        # Account/session state must not be closed while its worker is still active.
        await asyncio.gather(worker, return_exceptions=True)
