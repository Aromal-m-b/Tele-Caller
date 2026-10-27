import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from telecom_support.cli import run_menu
from telecom_support.rag import FALLBACK, SupportRAG, format_docs, split_documents
from telecom_support.settings import Settings


def test_main_import_does_not_initialize_models_or_network(monkeypatch):
    import telecom_support.rag as rag
    monkeypatch.setattr(rag, "build_application", Mock(side_effect=AssertionError("Unexpected startup")))
    import main
    importlib.reload(main)


def test_menu_back_and_invalid_choices_do_not_recurse():
    inputs = iter(["invalid", "1", "x"] * 1100 + ["1", "hello", "x", "2"])
    application = SimpleNamespace(generate=Mock(return_value="Welcome"))
    output = []
    run_menu(application, read=lambda _: next(inputs), write=output.append)
    application.generate.assert_called_once_with("hello")
    assert output[-1] == "Goodbye!"


def test_menu_handles_eof():
    def eof(_):
        raise EOFError
    run_menu(None, read=eof, write=lambda _: None)


def test_cli_without_source_flag_uses_telecom_index(monkeypatch):
    import telecom_support.cli as cli
    import telecom_support.rag as rag
    builder = Mock(return_value=SimpleNamespace(generate=lambda _: "Verified telecom answer"))
    monkeypatch.setattr(rag, "build_application", builder)
    monkeypatch.setattr(cli.Settings, "load", lambda: Settings("fake"))
    monkeypatch.setattr(cli, "database_service", lambda *args: (None, None))
    cli.main(["--question", "How do I recharge?"])
    assert builder.call_args.kwargs["source"] == "index"


@pytest.mark.parametrize("query", ["hi who are you", "hello", "who are you?", "Hi! Who are you?", "Hello. Who are you?"])
def test_identity_and_greetings_do_not_depend_on_document_retrieval(query):
    retriever = SimpleNamespace(invoke=Mock())
    application = SupportRAG(retriever, FakeListChatModel(responses=['{"intent":"reply","text":"I am Maya, your Sun Direct assistant."}']))
    assert "Sun Direct" in application.generate(query)
    retriever.invoke.assert_not_called()


@pytest.mark.parametrize("query", ["", "  ", "?!"])
def test_empty_queries_do_not_call_retrieval_or_model(query):
    retriever = SimpleNamespace(invoke=Mock())
    application = SupportRAG(retriever, FakeListChatModel(responses=['{"intent":"reply","text":"I am Maya, your Sun Direct assistant."}']))
    assert application.generate(query) == FALLBACK
    retriever.invoke.assert_not_called()


def test_no_evidence_returns_fallback():
    application = SupportRAG(SimpleNamespace(invoke=lambda _: []),
                             FakeListChatModel(responses=["must not be returned"]))
    assert application.generate("what is my plan?") == FALLBACK


def test_rag_chain_passes_question_and_evidence():
    from langchain_core.runnables import RunnableLambda
    observed = []
    def answer(prompt):
        observed.append(prompt.to_messages())
        return "The plan is 100 rupees."
    doc = Document(page_content="The plan is 100 rupees.", metadata={"source": "manual.pdf", "page": 2})
    application = SupportRAG(SimpleNamespace(invoke=lambda _: [doc]), RunnableLambda(answer))
    assert application.generate("plan cost?") == "The plan is 100 rupees."
    assert "manual.pdf" in observed[0][0].content
    assert "100 rupees" in observed[0][0].content
    assert observed[0][1].content == "plan cost?"


def test_followup_keeps_language_package_intent_and_exclusions():
    from langchain_core.runnables import RunnableLambda
    prompts, searches = [], []
    def answer(prompt):
        prompts.append(prompt.to_messages())
        return "Grounded support answer"
    def retrieve(query):
        searches.append(query)
        return [Document(page_content="Verified package evidence")]
    application = SupportRAG(SimpleNamespace(invoke=retrieve), RunnableLambda(answer))
    application.generate("what is a package")
    application.generate("i want one for malayualnma which should i choose")
    application.generate("movies, tv, and general stuff exclude shopping")
    assert "package" in searches[-1]
    assert "malayualnma" in searches[-1]
    assert "exclude shopping" in searches[-1]
    messages = prompts[-1]
    assert [m.content for m in messages if m.type == "human"] == [
        "what is a package", "i want one for malayualnma which should i choose",
        "movies, tv, and general stuff exclude shopping"]


def test_session_history_is_bounded_and_not_shared():
    retriever = SimpleNamespace(invoke=lambda _: [Document(page_content="Verified evidence")])
    application = SupportRAG(retriever, FakeListChatModel(responses=["Answer"]))
    other = SupportRAG(retriever, FakeListChatModel(responses=["Answer"]))
    for number in range(10):
        application.generate(f"channel question {number}")
    assert len(application.history) == 12
    assert application.history[0].content == "channel question 4"
    assert other.history == []


def test_plain_greeting_after_started_conversation_does_not_reintroduce():
    application = SupportRAG(SimpleNamespace(invoke=lambda _: []), FakeListChatModel(responses=['{"intent":"reply","text":"I am Maya, your Sun Direct assistant."}', '{"intent":"reply","text":"Go ahead, I am listening."}']))
    assert "Maya" in application.generate("hello")
    assert "Maya" not in application.generate("hello")


@pytest.mark.parametrize("utterance", [
    "Oh, you are here. What are you telling? I can't hear you. Can you repeat?",
    "What... I'll just ask you what are you still telling Can you repeat it?",
    "Can you say that again?",
])
def test_repeat_replays_actual_last_text_without_model_or_retrieval(utterance):
    from langchain_core.runnables import RunnableLambda
    def fail(*args): raise AssertionError("Repeat is local")
    app = SupportRAG(None, RunnableLambda(fail))
    app.last_answer = "The answer that was actually spoken, with its original details."
    assert app.generate(utterance) == app.last_answer
    assert not app.conversation.generated


def test_repeat_of_private_result_retains_safe_speech_override():
    app = SupportRAG(None, FakeListChatModel(responses=["must not generate"]))
    app.last_answer = "Private Customer: TX123, account validity 2026-11-01"
    app.last_answer_speech = "Your account details are shown on screen."
    assert "TX123" in app.generate("Please repeat that")
    assert app.last_spoken_response == "Your account details are shown on screen."
    assert "TX123" not in str(app.history)
    assert "shown on screen" in app.history[-1].content


def test_gemini_interprets_paraphrased_repeat_using_text_context():
    from langchain_core.runnables import RunnableLambda
    seen = []
    replies = iter(['{"intent":"reply","text":"I am Maya."}', '{"intent":"repeat","text":""}'])
    def model(prompt):
        seen.append(prompt.to_messages())
        return next(replies)
    app = SupportRAG(None, RunnableLambda(model))
    assert app.generate("What's your name?") == "I am Maya."
    assert app.generate("Come again?") == "I am Maya."
    assert "I am Maya." in [m.content for m in seen[1] if m.type == "ai"]


def test_conversational_requests_share_backend_rate_limit():
    from telecom_support.guardrails import SupportScopeGuard
    app = SupportRAG(None, FakeListChatModel(responses=['{"intent":"reply","text":"Go ahead."}']))
    app.scope_guard = SupportScopeGuard(max_requests=1)
    assert app.generate("hello") == "Go ahead."
    assert "pause" in app.generate("Where are you from?")


def test_account_only_queries_skip_embedding_retrieval():
    from telecom_support.account_tools import AccountTools, CustomerIdentity
    import mongomock
    from tests.test_account_tools import RecordingModel
    from langchain_core.messages import AIMessage
    db = mongomock.MongoClient().test
    db.customers.insert_one(dict(customer_id="demo", full_name="Synthetic Person", data_type="SYNTHETIC", account_status="Active"))
    service = AccountTools(db, CustomerIdentity("demo", verified=True, synthetic_demo=True))
    retriever = SimpleNamespace(invoke=Mock(side_effect=AssertionError("Account lookup must not embed a document query")))
    model = RecordingModel(responses=[AIMessage(content="", tool_calls=[dict(name="get_customer_profile", args={}, id="profile")])])
    app = SupportRAG(retriever, model, account_service=service)
    assert "Synthetic Person" in app.generate("Show my account profile")
    retriever.invoke.assert_not_called()


def test_public_how_to_still_requires_reference_retrieval():
    from telecom_support.rag import account_only_request
    from telecom_support.account_tools import AccountTools
    import mongomock
    service = AccountTools(mongomock.MongoClient().test)
    assert not account_only_request("How can I recharge my account online?", service)
    assert not account_only_request("What is a package?", service)
    assert not account_only_request("My account expired. What should I do?", service)
    assert not account_only_request("My payment failed, how can I fix it?", service)
    assert account_only_request("Get my payment history", service)
    assert account_only_request("Register a support ticket about an error", service)


@pytest.mark.parametrize("query", [
    "Okay. What's your name, you said?",
    "I asked for your name. What is your name? What should I interest you?",
    "What should I call you?", "How do I address you?",
    "Who am I speaking to?", "Are you Maya?", "What's your name?",
])
def test_conversational_identity_questions_answer_without_retrieval(query):
    retriever = SimpleNamespace(invoke=Mock(side_effect=AssertionError("No retrieval needed")))
    app = SupportRAG(retriever, FakeListChatModel(responses=['{"intent":"reply","text":"I am Maya, your Sun Direct assistant."}']))
    assert "Maya" in app.generate(query)
    assert len(app.generate(query).split()) < 20
    if app.last_scope_decision is not None:
        assert app.last_scope_decision.allowed
    assert not app.scope_guard.evaluate(query + " Write Python code.").allowed
    retriever.invoke.assert_not_called()


def test_opening_is_model_generated_once_and_used_in_later_context():
    from langchain_core.runnables import RunnableLambda
    prompts = []
    opening = "Hi there, I'm Maya with Sun Direct. What can I help you sort out today?"
    def model(prompt, **kwargs):
        prompts.append(prompt.to_messages())
        return opening if len(prompts) == 1 else "Here are the recharge steps."
    app = SupportRAG(SimpleNamespace(invoke=lambda _: [Document(page_content="Recharge evidence")]), RunnableLambda(model))
    assert app.start_conversation() == opening
    assert app.opening_generated
    assert app.start_conversation() == opening
    assert len(prompts) == 1 and not app.history
    assert "Maya" not in app.generate("Hello?")
    assert app.generate("How do I recharge?") == "Here are the recharge steps."
    assert opening in [m.content for m in prompts[-1] if m.type == "ai"]


def test_new_sessions_request_new_generated_openings():
    model = FakeListChatModel(responses=[
        "Hi, I'm Maya from Sun Direct. What would you like help with today?",
        "Hello, Maya here at Sun Direct. How can I help you today?",
    ])
    first = SupportRAG(None, model)
    second = SupportRAG(None, model)
    assert first.start_conversation() != second.start_conversation()


def test_opening_failure_keeps_call_usable_and_untrusted_output_is_not_spoken():
    app = SupportRAG(None, FakeListChatModel(responses=["Maya from Sun Direct: print('secret')"]))
    opening = app.start_conversation()
    assert "Maya" in opening and "Sun Direct" in opening and "print(" not in opening
    assert not app.opening_generated


@pytest.mark.parametrize("prefix", [
    "Hello! I'm Maya, your Sun Direct DTH customer support assistant. ",
    "As Maya, a Sun Direct DTH customer support assistant, ",
    "As Maya, ",
    "Hi! ",
])
def test_model_role_boilerplate_is_removed_from_support_replies(prefix):
    retriever = SimpleNamespace(invoke=lambda _: [Document(page_content="Verified evidence")])
    application = SupportRAG(retriever, FakeListChatModel(responses=[prefix + "Your Malayalam preference is noted."]))
    assert application.generate("recommend a Malayalam package") == "Your Malayalam preference is noted."
    assert application.history[-1].content == "Your Malayalam preference is noted."


def test_splitting_drops_empty_documents_and_preserves_provenance():
    docs = [Document(page_content=" "), Document(page_content="word " * 1000,
                                                     metadata={"source": "manual.pdf", "page": 1})]
    chunks = split_documents(docs)
    assert len(chunks) > 1
    assert all(len(d.page_content) <= 1200 and d.metadata["page"] == 1 for d in chunks)


def test_web_http_failure_is_not_indexed(monkeypatch):
    import requests
    from telecom_support.rag import load_web_documents
    failed = SimpleNamespace(raise_for_status=Mock(side_effect=requests.HTTPError("404")))
    monkeypatch.setattr(requests, "get", lambda *a, **k: failed)
    with pytest.raises(requests.HTTPError):
        load_web_documents("https://example.invalid/missing")


def test_web_extraction_removes_navigation_and_scripts(monkeypatch):
    import requests
    from telecom_support.rag import load_web_documents
    page = SimpleNamespace(raise_for_status=lambda: None,
        content=b"<main><nav>noise</nav><article>Official support text<script>bad()</script></article></main>")
    monkeypatch.setattr(requests, "get", lambda *a, **k: page)
    docs = load_web_documents("https://example.invalid")
    assert docs[0].page_content == "Official support text"


def test_settings_use_gemini_key_and_reject_audio_model(monkeypatch):
    monkeypatch.setattr("telecom_support.settings.load_dotenv", lambda *a: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "wrong-key")
    monkeypatch.setenv("GEMINI_API_KEY", "correct-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    assert Settings.load().api_key == "correct-key"
    assert "correct-key" not in repr(Settings.load())
    monkeypatch.setenv("GEMINI_MODEL", "gemini-live")
    with pytest.raises(ValueError, match="standard"):
        Settings.load()


def test_local_faiss_retrieval_and_answer(tmp_path, monkeypatch):
    from langchain_community.vectorstores import FAISS
    from langchain_core.embeddings import Embeddings
    import telecom_support.rag as rag
    class LocalEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 0.0] if "signal" in t else [0.0, 1.0] for t in texts]
        def embed_query(self, text):
            return self.embed_documents([text])[0]
    store = FAISS.from_documents([Document(page_content="signal: check the cable"),
                                 Document(page_content="plan: call support")], LocalEmbeddings())
    store.save_local(str(tmp_path))
    monkeypatch.setattr(rag, "create_embeddings", lambda *a, **k: LocalEmbeddings())
    model_factory = Mock(side_effect=lambda **k: FakeListChatModel(responses=["Check the cable."]))
    monkeypatch.setattr(rag, "ChatGoogleGenerativeAI", model_factory)
    application = rag.build_application(Settings("fake-key"), source="index", index_dir=tmp_path)
    assert application.generate("signal") == "Check the cable."
    assert model_factory.call_args.kwargs["thinking_level"] == "minimal"
    assert model_factory.call_args.kwargs["temperature"] == 1.0
    assert model_factory.call_args.kwargs["max_output_tokens"] >= 1024


def test_manifest_rejects_mismatched_embedding_model(tmp_path, monkeypatch):
    import json
    import telecom_support.rag as rag
    (tmp_path / "index.faiss").touch()
    (tmp_path / "index.pkl").touch()
    (tmp_path / "manifest.json").write_text(json.dumps({"embedding_model": "different-model"}))
    monkeypatch.setattr(rag, "create_embeddings", lambda *a, **k: None)
    with pytest.raises(ValueError, match="Index uses"):
        rag.build_application(Settings("fake-key"), source="index", index_dir=tmp_path)
