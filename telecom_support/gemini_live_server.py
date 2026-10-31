"""Same authenticated calling page, optional Google native audio demo backend."""
from .gemini_live import GeminiRuntime, livekit_runner
from .livekit_server import create_app

app = create_app(runtime=GeminiRuntime(),runner=livekit_runner,backend="gemini-live")
