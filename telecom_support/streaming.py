"""Sentence boundaries and a cancellable bridge from sync generation to speech."""
import asyncio
import re
import threading
from concurrent.futures import TimeoutError


def sentences(buffer, final=False):
    """Hold unfinished sentences (including decimal prices) until more tokens arrive."""
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\u201c])', buffer)
    ready = parts if final else parts[:-1]
    return [p.strip() for p in ready if p.strip()], "" if final else parts[-1]


async def generate_and_speak(application, query, speaker):
    queue = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()
    stopped = threading.Event()
    emitted = False

    def publish(text):
        if stopped.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(queue.put(text), loop)
        while not stopped.is_set():
            try:
                future.result(timeout=.1)
                return
            except TimeoutError:
                continue
        future.cancel()

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
        answer = await asyncio.shield(worker)
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
