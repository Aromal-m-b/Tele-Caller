import asyncio
import threading
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from telecom_support.rag import SupportRAG
from telecom_support.streaming import generate_and_speak, sentences


def test_sentence_boundary_preserves_decimal_and_partial_text():
    assert sentences("It costs 99.50 rupees. Next step") == (["It costs 99.50 rupees."], "Next step")
    assert sentences("Still unfinished") == ([], "Still unfinished")
    assert sentences("Last sentence", final=True) == (["Last sentence"], "")


def test_conversational_text_streams_before_json_response_finishes():
    from telecom_support.conversation import ConversationResponder
    from telecom_support.guardrails import SupportScopeGuard
    emitted = []
    def model(prompt):
        yield '{"intent":"reply","text":"Yes, I can hear you. Go'
        assert emitted == ['Yes, I can hear you.']
        yield ' ahead."}'
    responder = ConversationResponder(RunnableLambda(model), SupportScopeGuard())
    intent, text = responder.answer('Can you hear me?', dialogue=[], memory='', fallback='Retry', on_sentence=emitted.append)
    assert intent == 'reply' and text == 'Yes, I can hear you. Go ahead.'
    assert emitted == ['Yes, I can hear you.', 'Go ahead.']


def test_streamed_repeat_and_end_control_intentions_are_not_spoken_early():
    from telecom_support.conversation import ConversationResponder
    from telecom_support.guardrails import SupportScopeGuard
    for intent, text in [('repeat', ''), ('confirm_end', 'Shall I end the call?')]:
        import json
        responder = ConversationResponder(RunnableLambda(lambda _, i=intent, t=text: json.dumps(dict(intent=i, text=t))), SupportScopeGuard())
        emitted = []
        result = responder.answer('Thanks', dialogue=[], memory='', fallback='Retry', on_sentence=emitted.append)
        assert result == (intent, text)
        assert emitted == []


def test_conversation_stream_preserves_escaped_quotes_and_unicode_boundaries():
    from telecom_support.streaming import conversation_tokens
    parts = ['{"intent":"reply","text":"Call me \\"Maya\\". ', 'I can h', 'ear you. \\u00', 'e9"}']
    raw = []
    assert ''.join(conversation_tokens(iter(parts), raw)) == 'Call me "Maya". I can hear you. é'


def test_public_rag_emits_before_model_finishes_and_remembers_once():
    emitted = []
    def tokens(prompt):
        yield "Open the Sun Direct website. "
        yield "Choose recharge. "
        assert emitted == ["Open the Sun Direct website."]
        yield "Follow the payment instructions."
    app = SupportRAG(SimpleNamespace(invoke=lambda _: [Document(page_content="Recharge via website.")]),
                     RunnableLambda(tokens))
    answer = app.generate_stream("How can I recharge online?", emitted.append)
    assert len(emitted) == 3
    assert len(app.history) == 2
    assert app.history[-1].content == answer
    assert app._sentence_sink is None


def test_forbidden_request_never_streams_or_retrieves():
    def unexpected(_):
        raise AssertionError("Backend must not run")
    app = SupportRAG(SimpleNamespace(invoke=unexpected), RunnableLambda(unexpected))
    emitted = []
    app.generate_stream("How can I generate Python code?", emitted.append)
    assert emitted == []


def test_public_stream_works_with_database_tools_connected():
    def tokens(prompt):
        yield "Open the website. "
        yield "Choose recharge."
    app = SupportRAG(SimpleNamespace(invoke=lambda _: [Document(page_content="Website recharge instructions")]),
                     RunnableLambda(tokens))
    def unexpected(*args):
        raise AssertionError("Public how-to should not invoke account tools")
    app.account_agent = SimpleNamespace(service=SimpleNamespace(pending_recharge=None, ticket_service=None),
                                       last_calls=["stale_call"], answer=unexpected)
    spoken = []
    app.generate_stream("How can I recharge online?", spoken.append)
    assert len(spoken) == 2
    assert app.account_agent.last_calls == []


def test_private_tool_result_uses_buffered_safe_speech():
    app = SupportRAG(SimpleNamespace(invoke=lambda _: []), RunnableLambda(lambda _: "unused"))
    app.account_agent = SimpleNamespace(service=SimpleNamespace(pending_recharge=None, ticket_service=None),
        answer=lambda *args: ("Private account balance: fixture value", "Safe tool-use memory"),
        last_calls=["get_customer_profile"], last_spoken_response="Your details are on screen.")
    spoken = []
    answer = app.generate_stream("What is my balance?", spoken.append)
    assert "fixture value" in answer
    assert spoken == []
    assert app.last_spoken_response == "Your details are on screen."


def test_microphone_processor_does_not_speak_complete_reply_twice():
    from telecom_support.speech_input import process_turns, SpokenTurn
    spoken, output = [], []
    async def speak(text):
        spoken.append(text)
    def generate(query, publish):
        publish("First sentence.")
        publish("Second sentence.")
        return "First sentence. Second sentence."
    async def run():
        turns = asyncio.Queue()
        await turns.put(SpokenTurn("How can I recharge online?", 0))
        await turns.put(None)
        await process_turns(SimpleNamespace(generate_stream=generate), turns,
            speaker=SimpleNamespace(speak=speak, interrupted=False), write=output.append, idle_timeout=0)
    asyncio.run(run())
    assert spoken == ["First sentence.", "Second sentence."]
    assert "A: First sentence. Second sentence." in output


def test_bridge_speaks_before_generation_completes():
    played = threading.Event()
    async def speak(text):
        played.set()
    def generate(query, publish):
        publish("First sentence.")
        assert played.wait(2), "Speech waited for the full answer"
        publish("Second sentence.")
        return "First sentence. Second sentence."
    async def run():
        answer, emitted = await generate_and_speak(SimpleNamespace(generate_stream=generate), "query",
            SimpleNamespace(speak=speak, interrupted=False))
        assert emitted and answer.endswith("Second sentence.")
    asyncio.run(run())


def test_barge_in_discards_remaining_sentences():
    spoken = []
    speaker = SimpleNamespace(interrupted=False)
    async def speak(text):
        spoken.append(text)
        speaker.interrupted = True
    speaker.speak = speak
    def generate(query, publish):
        from telecom_support.streaming import StreamCancelled
        try:
            for i in range(30):
                publish(str(i))
        except StreamCancelled:
            pass
        return "Complete answer"
    asyncio.run(generate_and_speak(SimpleNamespace(generate_stream=generate), "query", speaker))
    assert spoken == ["0"]


def test_playback_failure_does_not_deadlock_producer():
    async def speak(text):
        raise RuntimeError("device failed")
    def generate(query, publish):
        from telecom_support.streaming import StreamCancelled
        try:
            for i in range(30):
                publish(str(i))
        except StreamCancelled:
            pass
        return "Answer"
    async def run():
        import pytest
        with pytest.raises(RuntimeError, match="device failed"):
            await asyncio.wait_for(generate_and_speak(SimpleNamespace(generate_stream=generate), "query",
                SimpleNamespace(speak=speak)), 3)
    asyncio.run(run())


def test_interrupted_generation_closes_provider_iterator_without_consuming_rest():
    import pytest
    from telecom_support.streaming import stream_sentences, StreamCancelled
    closed, seen = [], []
    def tokens():
        try:
            yield "First sentence. Next"
            seen.append('unwanted continuation')
            yield " sentence."
        finally:
            closed.append(True)
    def publish(text):
        raise StreamCancelled()
    with pytest.raises(StreamCancelled):
        stream_sentences(tokens(), publish, lambda value: value, lambda value: value, lambda value: None)
    assert closed == [True] and seen == []
