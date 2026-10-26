import asyncio
import logging
from .settings import ROOT, Settings


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gemini text reasoning and Deepgram audio")
    parser.add_argument("--text", action="store_true", help="Test reasoning without audio hardware")
    args = parser.parse_args()
    settings = Settings.load()
    logging.basicConfig(level=logging.INFO)
    from google import genai
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_community.vectorstores import FAISS
    from .agent import TextAgent

    embeddings = GoogleGenerativeAIEmbeddings(model=settings.embedding_model,
        google_api_key=settings.gemini_key, task_type="retrieval_query")
    # This project's trusted, locally generated index contains pickle data.
    store = FAISS.load_local(str(ROOT / "faiss_index"), embeddings,
                             allow_dangerous_deserialization=True)

    async def search(query):
        docs = await store.asimilarity_search(query, k=3)
        return "\n\n".join(doc.page_content for doc in docs) or "No matching company records."

    async with genai.Client(api_key=settings.gemini_key).aio as client:
        agent = TextAgent(client, settings.model, search)
        if args.text:
            while True:
                text = await asyncio.to_thread(input, "Customer (quit to exit): ")
                if text.strip().lower() in {"quit", "exit"}:
                    return
                if text.strip():
                    print("Maya:", await agent.reply(text))
        else:
            from .hardware_audio_io import HardwareAudioIO
            from .deepgram import DeepgramVoice
            from .orchestrator import VoiceOrchestrator
            audio = HardwareAudioIO()
            voice = DeepgramVoice(settings)
            try:
                audio.start_streams()
                await VoiceOrchestrator(audio, agent, voice).run()
            finally:
                await voice.close()
                audio.close()
