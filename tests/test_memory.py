from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableLambda

from telecom_support.memory import ConversationMemory
from telecom_support.rag import SupportRAG


def append(memory, question, answer="Acknowledged"):
    memory.append(HumanMessage(content=question), AIMessage(content=answer))


def test_older_exchanges_are_compressed_losslessly_and_appended():
    memory = ConversationMemory(recent_exchanges=2)
    original = "My preference is Malayalam. " * 100
    append(memory, original, "Please choose SD or HD.")
    append(memory, "second")
    append(memory, "third")
    append(memory, "fourth")
    assert len(memory.recent) == 4
    assert len(memory.notes) == 2
    assert memory.notes[0].messages() == [
        {"role": "human", "text": original},
        {"role": "ai", "text": "Please choose SD or HD."}]
    assert len(memory.notes[0].compressed) < len(original.encode())
    assert memory.notes[0].number < memory.notes[1].number


def test_old_preferences_and_issues_remain_retrievable_after_many_turns():
    memory = ConversationMemory()
    append(memory, "I want Malayalam movies, avoid shopping.")
    append(memory, "My CoconutBridge receiver has an intermittent audio fault.")
    for number in range(25):
        append(memory, f"Other question {number}")
    context = memory.context("Return to my CoconutBridge receiver issue")
    assert "Malayalam" in context
    assert "shopping" in context
    assert "CoconutBridge" in context
    assert "intermittent audio fault" in context
    assert len(memory.recent) == 12
    assert len(memory.notes) == 21
    assert len(memory.summary) <= 2400
    assert len(context) <= 5000


def test_corrections_have_chronological_provenance():
    memory = ConversationMemory(recent_exchanges=1)
    append(memory, "I prefer Malayalam.")
    append(memory, "Actually change my preference to Hindi instead.")
    append(memory, "What about the price?")
    context = memory.context("language preference")
    assert context.index("I prefer Malayalam") < context.index("Actually change")
    assert "Older exchange 1" in context and "Older exchange 2" in context


def test_memory_budget_and_reset_do_not_delete_other_sessions():
    first, second = ConversationMemory(), ConversationMemory()
    for number in range(40):
        append(first, f"I want item {number}. " * 100, "A long previous answer. " * 100)
    append(second, "Another customer")
    assert len(first.context("item", limit_chars=1000)) <= 1000
    assert first.context("item", limit_chars=0) == ""
    first.clear()
    assert not first.notes and not first.recent and not first.summary
    assert len(second.recent) == 2


def test_memory_reaches_retrieval_and_answer_without_extra_model_calls():
    prompts, searches = [], []
    def answer(prompt):
        prompts.append(prompt.to_messages())
        return "A grounded answer"
    def retrieve(query):
        searches.append(query)
        return [Document(page_content="Verified company reference")]
    application = SupportRAG(SimpleNamespace(invoke=retrieve), RunnableLambda(answer))
    application.generate("I want Malayalam movies, exclude shopping.")
    for number in range(10):
        application.generate(f"Channel question {number}")
    assert len(prompts) == 11  # Compression and lookup never call a model.
    assert len(application.history) == 12
    assert "Malayalam" not in str([m.content for m in application.history])
    assert "Malayalam" in searches[-1]
    assert "Malayalam" in prompts[-1][0].content
    assert "not verified evidence" in prompts[-1][0].content


def test_old_customer_preferences_can_be_recalled_without_company_documents():
    prompts = []
    def answer(prompt):
        prompts.append(prompt.to_messages())
        return "You said you prefer Malayalam."
    application = SupportRAG(SimpleNamespace(invoke=lambda _: []), RunnableLambda(answer))
    application._remember("I prefer Malayalam.", "Noted.")
    for number in range(10):
        application._remember(f"Other topic {number}", "Noted.")
    assert application.generate("Which language did I prefer earlier?") == "You said you prefer Malayalam."
    assert "I prefer Malayalam" in prompts[0][0].content
