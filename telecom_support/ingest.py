"""PDF folder extraction and safe index publication."""
import argparse
import json
import tempfile
from pathlib import Path

from .settings import ROOT, Settings


def extract_folder(folder, pipeline=None):
    from loaders.adaptive_pdf_loader import IngestionPipeline, ingest_pdf_adaptively
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"PDF folder does not exist: {folder}")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not files:
        raise ValueError(f"No PDF files found in {folder}")
    pipeline = pipeline or IngestionPipeline()
    documents = []
    for path in files:
        # Fail the build if any file fails instead of silently saving an incomplete index.
        extracted = ingest_pdf_adaptively(str(path), pipeline)
        documents.extend(extracted)
        print(f"{path.name}: {len(extracted)} nonempty pages")
    if not documents:
        raise ValueError("PDF extraction produced no nonempty documents.")
    return documents


def build_index(folder, destination, settings, *, overwrite=False):
    from langchain_community.vectorstores import FAISS
    from .rag import create_embeddings, split_documents
    destination = Path(destination).resolve()
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"Index destination already exists: {destination}; choose a new path or --overwrite.")
        if not destination.is_dir() or any(p.is_dir() for p in destination.iterdir()):
            raise ValueError("Overwrite is restricted to an index directory without subdirectories.")
    documents = extract_folder(folder)
    chunks = split_documents(documents)
    store = FAISS.from_documents(chunks, create_embeddings(settings, for_documents=True))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Persist completely before touching an existing index. Keep the previous files as a backup.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".index-build-") as staging:
        stage = Path(staging)
        store.save_local(str(stage))
        manifest = {"embedding_model": settings.embedding_model, "pages": len(documents),
                    "chunks": len(chunks), "source_folder": str(Path(folder).resolve())}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if destination.exists():
            backup = destination.with_name(destination.name + ".backup")
            if backup.exists():
                raise FileExistsError(f"Previous backup already exists: {backup}; choose a new destination.")
            destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            if overwrite and 'backup' in locals() and backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Adaptive PDF extraction and FAISS indexing")
    parser.add_argument("--assets", type=Path, default=ROOT / "assets")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "pdf_index")
    parser.add_argument("--extract-only", action="store_true", help="Extract without embedding or modifying an index")
    parser.add_argument("--overwrite", action="store_true", help="Back up and replace an existing index")
    args = parser.parse_args(argv)
    try:
        if args.extract_only:
            docs = extract_folder(args.assets)
            print(f"Extracted {len(docs)} pages; no index written.")
        else:
            manifest = build_index(args.assets, args.output, Settings.load(), overwrite=args.overwrite)
            print(f"Saved {manifest['chunks']} chunks to {args.output}")
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        parser.exit(1, f"Ingestion failed: {exc}\n")
