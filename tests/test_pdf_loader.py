from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image

from loaders.adaptive_pdf_loader import IngestionPipeline, PageType, ingest_pdf_adaptively, profile_page
from telecom_support.ingest import extract_folder


@pytest.fixture
def plain_pdf(tmp_path):
    path = tmp_path / "support.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), "Signal loss: check the cable and restart the receiver.")
        doc.new_page()  # blank pages must not reach embeddings
        doc.save(path)
    return path


def test_plain_extraction_is_local_and_filters_blank_pages(plain_pdf):
    pipeline = IngestionPipeline()
    docs = ingest_pdf_adaptively(str(plain_pdf), pipeline)
    assert len(docs) == 1
    assert "check the cable" in docs[0].page_content
    assert docs[0].metadata == {"source": str(plain_pdf), "page": 1, "engine": "pymupdf_native"}
    assert pipeline._ocr is pipeline._docling is pipeline._vlm is None
    # The PDF handle was closed and can be reopened for writing immediately.
    with plain_pdf.open("ab"):
        pass


def test_missing_and_corrupt_pdfs_fail(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_pdf_adaptively(str(tmp_path / "missing.pdf"))
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a PDF")
    with pytest.raises(pymupdf.FileDataError):
        ingest_pdf_adaptively(str(corrupt))


def test_password_protected_pdf_rejected(plain_pdf, tmp_path):
    locked = tmp_path / "locked.pdf"
    with pymupdf.open(plain_pdf) as doc:
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(ValueError, match="Password-protected"):
        ingest_pdf_adaptively(str(locked))


def test_profile_grid_as_table_instead_of_diagram():
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x in range(50, 451, 50):
            page.draw_line((x, 50), (x, 450))
        for y in range(50, 451, 50):
            page.draw_line((50, y), (450, y))
        page.insert_text((55, 70), "Plan")
        page.insert_text((105, 70), "Price")
        assert profile_page(page) == PageType.COMPLEX_LAYOUT_TABLE


def test_profile_sparse_vector_diagram():
    with pymupdf.open() as doc:
        page = doc.new_page()
        for n in range(30):
            page.draw_circle((50 + n % 5 * 90, 50 + n // 5 * 90), 15)
        assert profile_page(page) == PageType.VECTOR_DIAGRAM


def test_profile_simple_connected_boxes_as_diagram():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(100, 100, 400, 170))
        page.draw_rect(pymupdf.Rect(100, 240, 400, 310))
        page.draw_line((250, 170), (250, 240))
        page.insert_text((125, 140), "Signal loss")
        page.insert_text((125, 280), "Check receiver cable")
        assert profile_page(page) == PageType.VECTOR_DIAGRAM


@pytest.fixture
def scanned_page():
    doc = pymupdf.open()
    page = doc.new_page()
    image = Image.new("RGB", (600, 800), "white")
    import io
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    page.insert_image(page.rect, stream=stream.getvalue())
    yield page
    doc.close()


def test_scan_profile_and_paddle3_high_confidence(scanned_page):
    assert profile_page(scanned_page) == PageType.RASTER_SCAN
    pipeline = IngestionPipeline()
    pipeline._ocr = SimpleNamespace(predict=Mock(return_value=[{
        "rec_texts": ["Customer support: check receiver cable and then restart the device."],
        "rec_scores": [0.99]}]))
    pipeline._call_gemini_vision = Mock(side_effect=AssertionError("Unnecessary vision call"))
    result = pipeline.process_scan(scanned_page, "scan.pdf")
    assert result.metadata["engine"] == "paddleocr_local"
    assert result.metadata["confidence"] == 0.99
    pipeline._ocr.predict.assert_called_once()


def test_low_confidence_scan_escalates_to_standard_vision(scanned_page):
    pipeline = IngestionPipeline()
    pipeline._ocr = SimpleNamespace(predict=lambda _: [{"rec_texts": ["???"], "rec_scores": [0.1]}])
    pipeline._call_gemini_vision = Mock(return_value="Recovered support manual text")
    result = pipeline.process_scan(scanned_page, "scan.pdf")
    assert result.metadata["engine"] == "gemini_vlm_escalation"
    assert result.page_content == "Recovered support manual text"


def test_local_only_mode_never_uploads_pdf_pages(scanned_page):
    pipeline = IngestionPipeline(allow_remote_vision=False)
    with pytest.raises(RuntimeError, match="Remote PDF vision is disabled"):
        pipeline.process_diagram(scanned_page, "scan.pdf")
    assert pipeline._vlm is None


def test_docling_receives_only_target_page_and_rejects_partial_conversion(plain_pdf):
    from docling.datamodel.base_models import ConversionStatus
    pipeline = IngestionPipeline()
    def convert(stream):
        with pymupdf.open(stream=stream.stream.getvalue(), filetype="pdf") as sliced:
            assert len(sliced) == 1
            assert "Signal loss" in sliced[0].get_text()
        return SimpleNamespace(status=ConversionStatus.SUCCESS,
                               document=SimpleNamespace(export_to_markdown=lambda: "| Plan | Price |"))
    pipeline._docling = SimpleNamespace(convert=convert)
    with pymupdf.open(plain_pdf) as doc:
        result = pipeline.process_complex_page(doc[0], str(plain_pdf))
        assert result.metadata["page"] == 1
        assert result.page_content == "| Plan | Price |"
        pipeline._docling = SimpleNamespace(convert=lambda _: SimpleNamespace(status=ConversionStatus.PARTIAL_SUCCESS))
        with pytest.raises(RuntimeError, match="Docling conversion failed"):
            pipeline.process_complex_page(doc[0], str(plain_pdf))


def test_folder_build_fails_if_any_pdf_fails(plain_pdf, monkeypatch):
    import loaders.adaptive_pdf_loader as loader
    (plain_pdf.parent / "broken.pdf").write_bytes(b"broken")
    with pytest.raises(pymupdf.FileDataError):
        extract_folder(plain_pdf.parent)


def test_empty_folder_does_not_build_index(tmp_path):
    with pytest.raises(ValueError, match="No PDF"):
        extract_folder(tmp_path)


def test_existing_index_is_preserved_without_overwrite(tmp_path):
    from telecom_support.ingest import build_index
    from telecom_support.settings import Settings
    (tmp_path / "index.faiss").write_bytes(b"original-index")
    with pytest.raises(FileExistsError):
        build_index(tmp_path / "missing-assets", tmp_path, Settings("fake"))
    assert (tmp_path / "index.faiss").read_bytes() == b"original-index"


def test_index_build_manifest_and_backup(tmp_path, monkeypatch):
    import json
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    import telecom_support.ingest as ingest
    import telecom_support.rag as rag
    from telecom_support.settings import Settings
    class LocalEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]
        def embed_query(self, text):
            return [1.0, 0.0]
    monkeypatch.setattr(ingest, "extract_folder", lambda _: [Document(page_content="Verified support policy")])
    monkeypatch.setattr(rag, "create_embeddings", lambda *a, **k: LocalEmbeddings())
    destination = tmp_path / "new_index"
    result = ingest.build_index(tmp_path, destination, Settings("fake"))
    assert result["chunks"] == 1
    assert json.loads((destination / "manifest.json").read_text())["embedding_model"] == Settings.embedding_model
    original = (destination / "index.faiss").read_bytes()
    ingest.build_index(tmp_path, destination, Settings("fake"), overwrite=True)
    assert (tmp_path / "new_index.backup" / "index.faiss").read_bytes() == original
    with pytest.raises(FileExistsError, match="backup"):
        ingest.build_index(tmp_path, destination, Settings("fake"), overwrite=True)
