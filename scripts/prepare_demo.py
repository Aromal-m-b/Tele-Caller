"""Build a small, public fictional demo index; never overwrite an existing index."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import json
    from langchain_core.documents import Document
    from langchain_community.vectorstores import FAISS
    from telecom_support.settings import Settings
    from telecom_support.rag import create_embeddings, split_documents
    destination = ROOT / "faiss_index"
    if destination.exists():
        print("An index already exists; it has been preserved.")
        return
    settings = Settings.load()
    documents = [Document(page_content=p.read_text(encoding="utf-8"),
        metadata={"source": str(p.relative_to(ROOT)), "demo": True})
        for p in sorted((ROOT / "examples" / "knowledge").glob("*.md"))]
    if not documents:
        raise ValueError("No demo documents found.")
    store = FAISS.from_documents(split_documents(documents), create_embeddings(settings, for_documents=True))
    import tempfile
    with tempfile.TemporaryDirectory(dir=ROOT, prefix=".demo-index-") as temporary:
        stage = Path(temporary) / "index"
        store.save_local(str(stage))
        (stage / "manifest.json").write_text(json.dumps({"embedding_model": settings.embedding_model,
            "demo": True, "notice": "Fictional demo knowledge; not official policies or tariffs."}), encoding="utf-8")
        stage.rename(destination)
    print("Fictional demo index ready. Replace it with approved business knowledge before customer use.")


if __name__ == "__main__":
    main()
