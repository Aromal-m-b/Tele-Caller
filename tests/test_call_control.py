import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from telecom_support.call_control import CallControl
from telecom_support.speech_input import SpokenTurn, process_turns


@pytest.mark.parametrize("text", ["Hang up.", "Please end the call", "Can you disconnect this call?",
    "End call", "stop listening", "Could you close our conversation please?"])
def test_direct_requests_authorize_disconnect(text):
    assert CallControl().decide(text, started_at=0) == "end"


@pytest.mark.parametrize("text", ["Don't hang up", "Do not end the call", "What does end call mean?",
    "yes", "thank you", "My TV stopped working"])
def test_negation_questions_and_unprompted_yes_never_authorize_hangup(text):
    assert CallControl().decide(text, started_at=0) != "end"


def test_confirmation_requires_pending_prompt_and_later_turn():
    control = CallControl(clock=lambda: 10)
    assert control.decide("That's all", started_at=0) == "ask"
    control.asked()
    assert control.decide("yes please", started_at=9) == "ask"
    control.asked()
    assert control.decide("yes please", started_at=11) == "end"
    control.asked()
    assert control.decide("No thanks", started_at=11) == "stay"
    assert not control.pending
    control.asked()
    assert control.decide("What is my balance?", started_at=11) == "continue"
    assert not control.pending


def test_confirmed_call_end_does_not_reach_account_agent():
    async def run():
        app = SimpleNamespace(generate=Mock(side_effect=AssertionError("No backend calls")))
        turns = asyncio.Queue()
        for text in ["That's all", "yes please"]:
            turns.put_nowait(SpokenTurn(text, 11))
        output = []
        await process_turns(app, turns, write=output.append, clock=lambda: 10)
        assert any("end the call?" in line for line in output)
        assert "Goodbye!" in output[-1]
        app.generate.assert_not_called()
    asyncio.run(run())


def test_declining_hangup_keeps_session_and_does_not_confirm_recharge():
    async def run():
        app = SimpleNamespace(generate=Mock(side_effect=AssertionError("No backend calls")))
        turns = asyncio.Queue()
        for text in ["That's all", "no thanks"]:
            turns.put_nowait(SpokenTurn(text, 11))
        turns.put_nowait(None)
        output = []
        await process_turns(app, turns, write=output.append, clock=lambda: 10)
        assert any("keep the call open" in line for line in output)
        assert not any("Goodbye!" in line for line in output)
    asyncio.run(run())


def test_inactivity_warns_then_disconnects_without_backend():
    async def run():
        app = SimpleNamespace(generate=Mock(side_effect=AssertionError("No backend")))
        output = []
        await asyncio.wait_for(process_turns(app, asyncio.Queue(), write=output.append,
            idle_timeout=.02, idle_grace=.02), 1)
        assert "Are you still there?" in output[0]
        assert "end the call now" in output[-1]
        app.generate.assert_not_called()
    asyncio.run(run())


def test_recognized_user_activity_resets_idle_warning():
    async def run():
        warned = asyncio.Event()
        output = []
        control = CallControl()
        def write(text):
            output.append(text)
            if "Are you still there?" in text: warned.set()
        task = asyncio.create_task(process_turns(SimpleNamespace(), asyncio.Queue(), write=write,
            control=control, idle_timeout=.05, idle_grace=.05))
        await asyncio.wait_for(warned.wait(), 1)
        await asyncio.sleep(.02)
        control.touch()  # A recognized interim phrase, not raw microphone noise.
        await asyncio.sleep(.04)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())


def test_assistant_work_and_playback_do_not_consume_idle_budget():
    async def run():
        output = []
        class Speaker:
            async def speak(self, text): await asyncio.sleep(.03)
        app = SimpleNamespace(start_conversation=lambda: "Hello, I am Maya.")
        start = time.monotonic()
        await asyncio.wait_for(process_turns(app, asyncio.Queue(), write=output.append, speaker=Speaker(),
            idle_timeout=.02, idle_grace=.02), 1)
        assert time.monotonic() - start >= .12
        assert "Goodbye!" in output[-1] and output[-1] != "Listening..."
    asyncio.run(run())


def test_model_can_offer_disconnect_but_requires_user_confirmation():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from telecom_support.rag import SupportRAG
    async def run():
        app = SupportRAG(None, FakeListChatModel(responses=[
            '{"intent":"confirm_end","text":"Glad we could sort that out."}']))
        turns = asyncio.Queue()
        turns.put_nowait(SpokenTurn("I'm all set now, much appreciated", 11))
        turns.put_nowait(SpokenTurn("Yes please", 11))
        output = []
        await process_turns(app, turns, clock=lambda: 10, write=output.append)
        assert app.conversation.generated
        assert any("end the call?" in line for line in output)
        assert "Goodbye!" in output[-1]
    asyncio.run(run())


def test_idle_shutdown_closes_microphone_and_socket():
    from tests.test_speech_input import FakeSocket, FakeMicrophone
    from telecom_support.speech_input import SpeechSettings, run_microphone
    async def run():
        class Socket(FakeSocket):
            async def __aiter__(self):
                await asyncio.Event().wait()
                if False: yield None
        socket, microphone = Socket([]), FakeMicrophone()
        await asyncio.wait_for(run_microphone(SimpleNamespace(),
            SpeechSettings("fake", idle_timeout_seconds=.02, idle_grace_seconds=.02),
            microphone_factory=lambda **_: microphone, connect_factory=lambda *a, **k: socket,
            write=lambda _: None), 1)
        assert socket.closed and microphone.closed
    asyncio.run(run())


def test_direct_hangup_closes_session_and_speaks_goodbye_without_model():
    from unittest.mock import AsyncMock
    from tests.test_speech_input import FakeSocket, FakeMicrophone, result
    from telecom_support.speech_input import SpeechSettings, run_microphone
    async def run():
        class Speaker:
            epoch, mute_microphone = 0, False
            speak = AsyncMock()
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
        output = Speaker()
        socket = FakeSocket([result("Please disconnect this call", final=True, endpoint=True)])
        microphone = FakeMicrophone()
        app = SimpleNamespace(generate=Mock(side_effect=AssertionError("No model")))
        await run_microphone(app, SpeechSettings("fake", audio_output=True),
            microphone_factory=lambda **_: microphone, connect_factory=lambda *a, **k: socket,
            speaker_factory=lambda _: output, write=lambda _: None)
        app.generate.assert_not_called()
        output.speak.assert_awaited_once_with("Thanks for calling Sun Direct. Goodbye!")
        assert socket.closed and microphone.closed and output.closed
    asyncio.run(run())


def test_early_audibility_check_precedes_warning_and_goodbye():
    async def run():
        output = []
        await asyncio.wait_for(process_turns(SimpleNamespace(), asyncio.Queue(), write=output.append,
            idle_checkin=.01, idle_timeout=.03, idle_grace=.01), 1)
        assert "Hello again. Can you hear me?" in output[0]
        assert "Are you still there?" in output[1]
        assert "end the call now" in output[2]
        assert len(output) == 3
    asyncio.run(run())


def test_yes_to_audibility_check_does_not_confirm_recharge_or_disconnect():
    async def run():
        turns, output = asyncio.Queue(), []
        app = SimpleNamespace(generate=Mock(side_effect=AssertionError("Presence answer must stay local")))
        def write(text):
            output.append(text)
            if "Hello again" in text:
                turns.put_nowait(SpokenTurn("Yes please", time.monotonic()))
                turns.put_nowait(None)
        await asyncio.wait_for(process_turns(app, turns, write=write,
            idle_checkin=.01, idle_timeout=.03, idle_grace=.02), 1)
        assert any("Thanks, I'm here" in text for text in output)
        assert not any("Goodbye!" in text for text in output)
        app.generate.assert_not_called()
    asyncio.run(run())


def test_idle_lines_are_generated_by_model_with_dialogue_context():
    from langchain_core.runnables import RunnableLambda
    from telecom_support.rag import SupportRAG
    async def run():
        seen = []
        replies = iter(["Still with me? Is the audio coming through?",
                        "Are you there? I'll need to wrap up soon if I don't hear back.",
                        "I'll leave it there for now. Take care!"])
        def model(prompt):
            seen.append(prompt.to_messages())
            return next(replies)
        app = SupportRAG(None, RunnableLambda(model))
        app.opening_message = "Maya from Sun Direct here."
        # Avoid a second startup generation in this isolated processor check.
        app.start_conversation = lambda: app.opening_message
        app._remember("My audio was faint", "Please check the volume.")
        output = []
        await asyncio.wait_for(process_turns(app, asyncio.Queue(), write=output.append,
            idle_checkin=.01, idle_timeout=.03, idle_grace=.01), 2)
        assert "A: Still with me? Is the audio coming through?" in output
        assert output[-1] == "A: I'll leave it there for now. Take care!"
        assert len(seen) == 3
        assert "My audio was faint" in [m.content for m in seen[0] if m.type == "human"]
        assert "Still with me?" in seen[1][0].content
    asyncio.run(run())


def test_user_reply_during_idle_generation_prevents_stale_disconnect():
    async def run():
        turns = asyncio.Queue()
        control = CallControl()
        output = []
        def generate(event):
            # Simulate caller activity arriving while Gemini prepares the hint.
            if event == "idle_hint":
                control.touch()
            return "Can you still hear me?"
        app = SimpleNamespace(generate_call_message=generate)
        task = asyncio.create_task(process_turns(app, turns, write=output.append, control=control,
            idle_checkin=.01, idle_timeout=.04, idle_grace=.02))
        await asyncio.sleep(.06)
        assert not task.done()
        assert not output  # Stale generated hints weren't spoken over activity.
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
