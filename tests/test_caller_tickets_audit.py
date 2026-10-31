import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import mongomock
import pytest

from telecom_support.account_tools import AccountTools, ToolError
from telecom_support.caller import CallerSession, seed_demo_caller
from telecom_support.call_audit import CallAudit
from telecom_support.tickets import TicketService, render_ticket


@pytest.fixture
def account():
    db = mongomock.MongoClient().test
    seed_demo_caller(db, "0000000000")
    db.customers.update_one({}, {"$set": {"smartcard_id": "TEST-SC-0102"}})
    service = AccountTools(db)
    service.caller_session = CallerSession(service, "0000000000")
    return service


@pytest.fixture
def template(tmp_path):
    p = tmp_path / "template.docx"
    body = '<w:p><w:r><w:t>{name}</w:t></w:r></w:p><w:p><w:r><w:t>{description}</w:t></w:r></w:p>'
    body += '<w:p><w:r><w:t>{^hasAttachments}No attachments{/hasAttachments}</w:t></w:r></w:p>'
    body += '<w:p><w:r><w:t>{#relatedTickets}</w:t></w:r></w:p><w:p><w:r><w:t>{ticketId}: {ticketSummary}</w:t></w:r></w:p><w:p><w:r><w:t>{/relatedTickets}</w:t></w:r></w:p>'
    with ZipFile(p, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        z.writestr("word/styles.xml", "preserve-exactly")
    return p


def test_seed_is_idempotent_and_never_overwrites_real_customer():
    db = mongomock.MongoClient().test
    row, created = seed_demo_caller(db, "0000000000")
    assert created and row["data_type"] == "SYNTHETIC"
    assert not seed_demo_caller(db, "0000000000")[1]
    db.customers.update_one({"customer_id": row["customer_id"]}, {"$set": {"data_type": "REAL"}})
    with pytest.raises(ValueError): seed_demo_caller(db, "0000000000")
    service = AccountTools(db)
    assert not CallerSession(service, "0000000000").customer


def test_registered_phone_binds_synthetic_identity(account):
    assert account.identity.verified
    assert account.caller_session.display_name == "Demo Caller"


def test_unregistered_number_can_verify_name_and_card_locally(account):
    caller = CallerSession(account, "1111111111")
    account.identity = None
    assert not caller.customer
    assert caller.begin_verification() == "verify_name"
    assert caller.consume_verification("My name is Demo Caller.") == "verify_card"
    assert caller.consume_verification("TEST SC 0102") == "caller_verified"
    assert account.identity.verified and caller.customer


def test_wrong_card_and_attempt_limit_do_not_authenticate(account):
    account.identity = None
    caller = CallerSession(account, "1111111111")
    for _ in range(3):
        caller.begin_verification()
        caller.consume_verification("demo_caller")
        assert caller.consume_verification("wrong") == "verification_failed"
    assert caller.begin_verification() == "verification_unavailable"
    assert not caller.customer and account.identity is None


def test_ticket_draft_does_not_write_until_confirmed(account, template, tmp_path):
    tickets = TicketService(account, template, tmp_path / "tickets", call_id="call-test")
    draft = tickets.prepare("Reception", "Screen shows an error message")
    assert draft["confirmation_required"]
    assert account.db.support_tickets.count_documents({}) == 0
    result = tickets.confirm()
    assert result["status"] == "Open" and Path(result["document"]).exists()
    assert account.db.support_tickets.count_documents({}) == 1
    row = account.db.support_tickets.find_one({})
    assert row["name"] == "Demo Caller" and row["call_id"] == "call-test"
    with pytest.raises(ToolError): tickets.confirm()


def test_guest_cannot_create_ticket(account, template, tmp_path):
    account.identity = None
    with pytest.raises(ToolError): TicketService(account, template, tmp_path).prepare("Reception", "An error message")
    assert account.db.support_tickets.count_documents({}) == 0


def test_template_repeats_related_tickets_and_preserves_other_parts(template, tmp_path):
    dest = tmp_path / "result.docx"
    render_ticket(template, dest, dict(name="Demo", description="A literal {name} in user text", hasAttachments=[],
        relatedTickets=[dict(ticketId="T1", ticketSummary="First"), dict(ticketId="T2", ticketSummary="Second")]))
    with ZipFile(dest) as z:
        xml = z.read("word/document.xml").decode()
        assert "T1: First" in xml and "T2: Second" in xml
        assert "No attachments" in xml and "{#" not in xml
        assert "A literal {name} in user text" in xml
        assert z.read("word/styles.xml") == b"preserve-exactly"


def test_call_log_usage_cost_and_redaction(tmp_path):
    now = [0]
    audit = CallAudit(tmp_path, caller="synthetic", model="gemini-3-flash-preview", clock=lambda: now[0])
    audit.redact("CARD-SECRET")
    audit.stt_connected()
    audit.event("You: CARD-SECRET")
    audit.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 25}))]]))
    audit.synthesized(1000)
    now[0] = 60
    audit.close(); audit.close()
    text = audit.path.read_text()
    assert "CARD-SECRET" not in text and "redacted" in text
    usage = json.loads(text.split("USAGE AND COST\n")[1])
    assert usage["total_tokens"] == 125
    assert usage["estimated_total_usd"] == pytest.approx(.037825)
    assert text.count("USAGE AND COST") == 1


def test_display_name_is_sent_only_for_greeting_events(account):
    from langchain_core.runnables import RunnableLambda
    from telecom_support.rag import SupportRAG
    seen = []
    def model(prompt):
        seen.append(str(prompt.to_messages()))
        return "Hello Demo Caller, I'm Maya from Tech Five. What can I help you with today?"
    app = SupportRAG(None, RunnableLambda(model))
    app.caller_session = account.caller_session
    app.start_conversation()
    assert "Demo Caller" in seen[0]
    app.generate_call_message("goodbye")
    assert "Demo Caller" not in seen[1]
    assert "TEST-SC-0102" not in "".join(seen)


def test_verification_card_never_reaches_model_or_support_memory(account):
    from langchain_core.runnables import RunnableLambda
    from telecom_support.rag import SupportRAG
    account.identity = None
    caller = CallerSession(account, "1111111111")
    seen = []
    def model(prompt):
        seen.append(str(prompt.to_messages()))
        return "Your synthetic account is verified. What can I help with?"
    app = SupportRAG(None, RunnableLambda(model))
    app.caller_session = caller
    caller.begin_verification()
    app.generate("demo_caller")
    app.generate("TEST SC zero one zero two")
    assert caller.customer
    assert "TEST SC zero one zero two" not in "".join(seen)
    assert "demo_caller" not in "".join(seen)
    assert not app.history


def test_ticket_confirmation_tool_cannot_be_requested_by_model():
    from telecom_support.tool_agent import DEFINITIONS
    assert "confirm_support_ticket" not in DEFINITIONS


def test_text_only_call_has_no_stt_charge_and_missing_usage_is_visible(tmp_path):
    now = [0]
    audit = CallAudit(tmp_path, model="gemini-3-flash-preview", clock=lambda: now[0])
    audit.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata=None))]]))
    now[0] = 60
    audit.close()
    text = audit.path.read_text()
    summary = json.loads(text.split("USAGE AND COST\n")[1])
    assert summary["stt_conservative_estimate_usd"] == 0
    assert summary["requests_without_usage_metadata"] == 1


def test_short_display_names_are_masked_outside_greetings(account):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from telecom_support.rag import SupportRAG
    account.caller_session.customer["full_name"] = "Al Example"
    app = SupportRAG(None, FakeListChatModel(responses=["unused"]))
    app.caller_session = account.caller_session
    assert app.safe_model_text("Hello Al, welcome back.") == "Hello [customer], welcome back."
