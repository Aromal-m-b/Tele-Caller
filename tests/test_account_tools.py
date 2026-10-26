from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import mongomock
import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from pydantic import Field

from telecom_support.account_tools import AccountTools, CustomerIdentity, INDIA
from telecom_support.rag import SupportRAG
from telecom_support.tool_agent import ReadOnlyToolAgent, create_tools


@pytest.fixture
def db():
    database = mongomock.MongoClient().telecaller
    database.customers.insert_many([
        {"customer_id": "C1", "full_name": "Synthetic Owner", "phone": "0000000001", "email": "owner@example.invalid", "account_status": "Active", "data_type": "SYNTHETIC"},
        {"customer_id": "C2", "full_name": "Other Owner", "account_status": "Active", "data_type": "SYNTHETIC"}])
    database.bouquets.insert_many([
        {"bouquet_id": "B1", "bouquet_name": "Fixture Malayalam Movies", "primary_language": "Malayalam", "monthly_price_inr": 150, "is_active": True},
        {"bouquet_id": "B2", "bouquet_name": "Fixture Mixed Shopping", "primary_language": "Malayalam", "monthly_price_inr": 120, "is_active": "True"}])
    database.channels.insert_many([
        {"channel_id": "CH1", "channel_name": "Fixture Movies", "language": "Malayalam", "genre": "Movies", "monthly_price_inr": 10, "is_active": True, "is_hd": "False"},
        {"channel_id": "CH2", "channel_name": "Fixture Entertainment", "language": "Malayalam", "genre": "Entertainment", "monthly_price_inr": 5, "is_active": "True", "is_hd": True},
        {"channel_id": "CH3", "channel_name": "Fixture Shopping", "language": "Hindi", "genre": "Shopping", "monthly_price_inr": 1, "is_active": True, "is_hd": False}])
    database.bouquet_channels.insert_many([
        {"bouquet_id": "B1", "channel_id": "CH1"}, {"bouquet_id": "B1", "channel_id": "CH2"},
        {"bouquet_id": "B2", "channel_id": "CH1"}, {"bouquet_id": "B2", "channel_id": "CH3"}])
    database.subscriptions.insert_many([
        {"subscription_id": "S1", "customer_id": "C1", "bouquet_id": "B1", "start_date": "2026-10-01", "expiry_date": "2026-10-31", "subscription_status": "Active", "auto_renew": "False"},
        {"subscription_id": "S2", "customer_id": "C2", "bouquet_id": "B2", "start_date": "2026-10-01", "expiry_date": "2026-10-31", "subscription_status": "Active", "auto_renew": True}])
    database.payments.insert_many([
        {"payment_id": "P1", "customer_id": "C1", "subscription_id": "S1", "payment_date": "2026-10-01", "amount_inr": 150, "payment_status": "Successful", "payment_method": "UPI"},
        {"payment_id": "P2", "customer_id": "C1", "subscription_id": "S1", "payment_date": "2026-10-20", "amount_inr": 150, "payment_status": "Failed", "payment_method": "Card"},
        {"payment_id": "P_OTHER", "customer_id": "C2", "subscription_id": "S2", "payment_date": "2026-10-22", "amount_inr": 120, "payment_status": "Successful", "payment_method": "UPI"}])
    return database


@pytest.fixture
def service(db):
    return AccountTools(db, CustomerIdentity("C1", verified=True, synthetic_demo=True),
                        now=lambda: datetime(2026, 10, 25, 12, tzinfo=INDIA))


@pytest.mark.parametrize("name,args", [
    ("get_customer_profile", {}), ("get_active_plan", {}), ("get_active_channels", {}),
    ("get_payment_history", {}), ("check_payment_status", {"payment_id": "P1"}),
    ("get_previous_recharge", {})])
def test_private_tools_require_verified_identity(db, name, args):
    assert AccountTools(db).execute(name, args)["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_identity_cannot_be_supplied_by_model(service):
    assert service.execute("get_customer_profile", {"customer_id": "C2"})["error"]["code"] == "INVALID_ARGUMENTS"
    assert service.execute("check_payment_status", {"payment_id": "P_OTHER"})["error"]["code"] == "PAYMENT_NOT_FOUND"
    assert service.execute("check_payment_status", {"payment_id": {"$ne": None}})["error"]["code"] == "INVALID_PAYMENT_ID"


def test_previous_recharge_skips_failed_payment_and_joins_owned_package(service):
    result = service.execute("get_previous_recharge", {})
    assert result["ok"] and result["payment"]["payment_id"] == "P1"
    assert result["subscription"]["bouquet"]["bouquet_id"] == "B1"
    assert result["confirmation_required"] and not result["payment_executed"]


def test_previous_recharge_rejects_broken_ownership(service, db):
    db.payments.update_one({"payment_id": "P1"}, {"$set": {"subscription_id": "S2"}})
    assert service.execute("get_previous_recharge", {})["error"]["code"] == "PAYMENT_SUBSCRIPTION_MISMATCH"


def test_validity_uses_dates_not_just_active_label(service, db):
    assert service.execute("get_active_plan", {})["has_active_plan"]
    assert service.execute("get_active_plan", {})["subscription"]["auto_renew"] is False
    db.subscriptions.update_one({"subscription_id": "S1"}, {"$set": {"expiry_date": "2026-10-24"}})
    result = service.execute("get_active_plan", {})
    assert not result["has_active_plan"]
    assert result["subscription"]["expiry_date"] == "2026-10-24"


def test_expiry_day_is_inclusive_and_future_start_is_invalid(service, db):
    db.subscriptions.update_one({"subscription_id": "S1"}, {"$set": {"expiry_date": "2026-10-25"}})
    assert service.execute("get_active_plan", {})["has_active_plan"]
    db.subscriptions.update_one({"subscription_id": "S1"}, {"$set": {"start_date": "2026-10-26", "expiry_date": "2026-11-25"}})
    assert not service.execute("get_active_plan", {})["has_active_plan"]


def test_addon_channels_are_deduplicated_and_expired_addons_ignored(service, db):
    db.subscription_channels.insert_many([
        {"subscription_id": "S1", "channel_id": "CH1", "status": "Active", "activated_on": "2026-10-01"},
        {"subscription_id": "S1", "channel_id": "CH3", "status": "Expired", "activated_on": "2026-10-01"}])
    ids = [row["channel_id"] for row in service.execute("get_active_channels", {})["channels"]]
    assert ids == ["CH2", "CH1"] and len(ids) == len(set(ids))


def test_catalog_recommendation_checks_all_channels_for_exclusions(service):
    result = service.execute("recommend_plan", {"language": "Malayalam", "genres": ["Movies", "Entertainment"], "exclude_genres": ["Shopping"]})
    assert result["ok"] and [row["bouquet_id"] for row in result["recommendations"]] == ["B1"]
    assert "illustrative" in result["data_notice"]
    assert service.execute("recommend_plan", {"language": "Malayalam", "max_price_inr": 50})["recommendations"] == []


def test_payment_history_order_and_limit(service):
    payments = service.execute("get_payment_history", {"limit": 1})["payments"]
    assert len(payments) == 1 and payments[0]["payment_id"] == "P2"
    assert service.execute("get_payment_history", {"limit": 999})["error"]["code"] == "INVALID_LIMIT"


def test_confirmation_requires_later_explicit_turn_and_never_writes(service, db):
    before = {name: list(db[name].find()) for name in db.list_collection_names()}
    service.begin_turn("yes")
    service.execute("get_previous_recharge", {})
    assert service.execute("confirm_recharge_choice", {})["error"]["code"] == "SELECTION_NOT_YET_PRESENTED"
    service.begin_turn("yes but change the package")
    assert service.execute("confirm_recharge_choice", {})["error"]["code"] == "EXPLICIT_CONFIRMATION_REQUIRED"
    service.begin_turn("yes")
    result = service.execute("confirm_recharge_choice", {})
    assert result["ok"] and result["selection_confirmed"]["bouquet_id"] == "B1"
    assert not result["payment_executed"] and not result["subscription_changed"]
    assert before == {name: list(db[name].find()) for name in db.list_collection_names()}


def test_confirmation_rechecks_catalog_changes(service, db):
    service.begin_turn("continue the previous recharge")
    service.execute("get_previous_recharge", {})
    db.bouquets.update_one({"bouquet_id": "B1"}, {"$set": {"is_active": False}})
    service.begin_turn("confirm")
    assert service.execute("confirm_recharge_choice", {})["error"]["code"] == "SELECTION_CHANGED"


def test_plain_yes_is_handled_locally_without_sending_results_to_model(service):
    service.begin_turn("previous recharge")
    service.execute("get_previous_recharge", {})
    bound = Mock()
    bound.invoke.side_effect = AssertionError("No model call needed to confirm a presented selection")
    model = SimpleNamespace(bind_tools=lambda tools: bound)
    agent = ReadOnlyToolAgent(model, service)
    reply, history = agent.answer([], "yes")
    assert "choice only" in reply and "No payment" in reply
    assert agent.last_calls == ["confirm_recharge_choice"]
    assert service.pending_recharge is None
    bound.invoke.assert_not_called()


def test_punctuated_voice_confirmation_is_accepted(service):
    service.begin_turn("previous recharge")
    service.execute("get_previous_recharge", {})
    service.begin_turn("Yes, please.")
    assert service.execute("confirm_recharge_choice", {})["ok"]


def test_plain_no_cancels_pending_choice_locally(service):
    service.begin_turn("previous recharge")
    service.execute("get_previous_recharge", {})
    model = SimpleNamespace(bind_tools=lambda tools: Mock())
    agent = ReadOnlyToolAgent(model, service)
    reply, _ = agent.answer([], "no thanks")
    assert "cancelled" in reply and service.pending_recharge is None


def test_demo_rejects_non_synthetic_customer_and_duplicate_identity(service, db):
    db.customers.update_one({"customer_id": "C1"}, {"$set": {"data_type": "REAL"}})
    assert service.execute("get_customer_profile", {})["error"]["code"] == "DEMO_IDENTITY_REJECTED"
    db.customers.insert_one({"customer_id": "C1", "data_type": "SYNTHETIC"})
    assert service.execute("get_customer_profile", {})["error"]["code"] == "AMBIGUOUS_CUSTOMER"


def test_model_tool_schemas_never_expose_customer_id(service):
    for item in create_tools(service):
        assert "customer_id" not in item.args_schema.model_json_schema()["properties"]


class RecordingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)
    def bind_tools(self, tools, **kwargs):
        return self
    def invoke(self, input, config=None, **kwargs):
        messages = input.to_messages() if hasattr(input, "to_messages") else list(input)
        self.seen.append(str(messages))
        return super().invoke(input, config=config, **kwargs)


def test_database_results_never_enter_model_messages_or_compressed_memory(service):
    calls = [AIMessage(content="", tool_calls=[{"name": "get_customer_profile", "args": {}, "id": "one"}]),
             AIMessage(content='{"intent":"reply","text":"Hello again."}'),
             AIMessage(content="", tool_calls=[{"name": "get_active_plan", "args": {}, "id": "two"}])]
    model = RecordingModel(responses=calls)
    application = SupportRAG(SimpleNamespace(invoke=lambda _: []), model, account_service=service)
    output = application.generate("Show my account profile")
    assert "Synthetic Owner" in output
    assert "Synthetic Owner" not in application.last_spoken_response
    assert "owner@example.invalid" not in application.last_spoken_response
    application.generate("hello")
    assert application.last_spoken_response is None
    application.generate("What is my package validity?")
    assert len(model.seen) == 3
    assert all("Synthetic Owner" not in prompt and "owner@example.invalid" not in prompt for prompt in model.seen)
    assert "Synthetic Owner" not in str(application.history)
    assert "owner@example.invalid" not in application.memory.summary
