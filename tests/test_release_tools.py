from types import SimpleNamespace

import pytest

from scripts.estimate_costs import estimate


def test_cost_model_arithmetic_and_break_even():
    result = estimate()
    assert result["variable_cost_per_call"] == .0896
    assert result["monthly_total_cost"] == 269.6
    assert result["potential_staff_hours_released"] == 40
    assert result["capacity_value_minus_operating_cost"] == 210.4
    assert result["break_even_calls_at_assumed_containment"] == 462


@pytest.mark.parametrize("overrides", [{"containment": 1.1}, {"calls": -1}, {"minutes": float("nan")}])
def test_invalid_economics_inputs_are_rejected(overrides):
    with pytest.raises(ValueError): estimate(**overrides)


def test_no_containment_has_no_capacity_break_even():
    result = estimate(containment=0)
    assert result["break_even_calls_at_assumed_containment"] is None
    assert result["capacity_value_minus_operating_cost"] < 0


def test_demo_builder_preserves_existing_index_without_api_call(tmp_path, monkeypatch):
    from scripts import prepare_demo
    from telecom_support.settings import Settings
    (tmp_path / "faiss_index").mkdir()
    original = tmp_path / "faiss_index" / "index.faiss"
    original.write_bytes(b"existing index")
    monkeypatch.setattr(prepare_demo, "ROOT", tmp_path)
    def fail(): raise AssertionError("Existing index must not need a key or API call")
    monkeypatch.setattr(Settings, "load", fail)
    prepare_demo.main()
    assert original.read_bytes() == b"existing index"


def test_demo_index_can_be_built_with_local_embeddings(tmp_path, monkeypatch):
    from scripts import prepare_demo
    from telecom_support import rag
    from telecom_support.settings import Settings
    from langchain_core.embeddings import DeterministicFakeEmbedding
    folder = tmp_path / "examples" / "knowledge"
    folder.mkdir(parents=True)
    (folder / "demo.md").write_text("Fictional support knowledge.", encoding="utf-8")
    monkeypatch.setattr(prepare_demo, "ROOT", tmp_path)
    monkeypatch.setattr(Settings, "load", lambda: SimpleNamespace(embedding_model="fake"))
    monkeypatch.setattr(rag, "create_embeddings", lambda *a, **k: DeterministicFakeEmbedding(size=8))
    prepare_demo.main()
    assert (tmp_path / "faiss_index" / "index.faiss").is_file()
    assert '"demo": true' in (tmp_path / "faiss_index" / "manifest.json").read_text()
