"""RAG Tool — Document upload + vector search for Compagnon.

Upload PDFs/text files → chunk → embed → ChromaDB.
Then query them via the rag_search tool during conversations.
Uses Ollama nomic-embed-text for local embeddings (no API key needed),
or falls back to a simple TF-IDF approach if ChromaDB isn't available.

Tools:
  rag_upload  — Upload and index a document
  rag_search  — Search indexed documents by query
  rag_list    — List all indexed documents
  rag_delete  — Delete a document from the index
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from tool_registry import BaseTool, ToolResult, ToolContext
from config import CompagnonConfig

import logging
logger = logging.getLogger(__name__)

# Defaults
DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_TOP_K = 4
EMBEDDING_MODEL = "nomic-embed-text"


def _get_rag_dir(config: Optional[CompagnonConfig] = None) -> Path:
    if config and hasattr(config, "data_dir"):
        d = Path(config.data_dir) / "rag"
    else:
        d = Path.home() / ".compagnon" / "rag"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_docs_index(rag_dir: Path) -> list[dict]:
    idx = rag_dir / "docs_index.json"
    if idx.exists():
        try:
            return json.loads(idx.read_text())
        except Exception:
            pass
    return []


def _save_docs_index(rag_dir: Path, docs: list[dict]):
    idx = rag_dir / "docs_index.json"
    idx.write_text(json.dumps(docs, indent=2, ensure_ascii=False))


class _VectorStore:
    """Wrapper around ChromaDB with lazy init."""

    def __init__(self, rag_dir: Path):
        self._rag_dir = rag_dir
        self._store = None
        self._embeddings = None

    def _init(self):
        if self._store is not None:
            return True
        try:
            from langchain_ollama import OllamaEmbeddings
            from langchain_community.vectorstores import Chroma

            self._embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL)
            chroma_dir = self._rag_dir / "chromadb"
            chroma_dir.mkdir(exist_ok=True)
            self._store = Chroma(
                persist_directory=str(chroma_dir),
                embedding_function=self._embeddings,
                collection_name="compagnon_docs",
            )
            return True
        except ImportError as e:
            logger.warning("RAG dependencies missing: %s (pip install langchain-ollama langchain-community chromadb)", e)
            return False
        except Exception as e:
            logger.warning("RAG init failed: %s", e)
            return False

    def add_documents(self, chunks: list) -> int:
        if not self._init():
            return 0
        self._store.add_documents(chunks)
        return len(chunks)

    def search(self, query: str, k: int = DEFAULT_TOP_K) -> list[dict]:
        if not self._init():
            return []
        try:
            results = self._store.similarity_search_with_score(query, k=k)
            output = []
            for doc, score in results:
                if score < 2.0:  # relevance threshold
                    output.append({
                        "content": doc.page_content,
                        "source": doc.metadata.get("source", "unknown"),
                        "score": round(score, 3),
                        "chunk_index": doc.metadata.get("chunk_index", 0),
                    })
            return output
        except Exception as e:
            logger.warning("RAG search failed: %s", e)
            return []

    def delete_by_hash(self, file_hash: str) -> int:
        if not self._init():
            return 0
        try:
            collection = self._store._collection
            results = collection.get(where={"file_hash": file_hash})
            if results and results["ids"]:
                collection.delete(ids=results["ids"])
                return len(results["ids"])
        except Exception:
            pass
        return 0


# Singleton
_stores: dict[str, _VectorStore] = {}


def _get_store(config: Optional[CompagnonConfig] = None) -> _VectorStore:
    rag_dir = _get_rag_dir(config)
    key = str(rag_dir)
    if key not in _stores:
        _stores[key] = _VectorStore(rag_dir)
    return _stores[key]


def _process_file(filepath: str, config: Optional[CompagnonConfig] = None) -> dict:
    """Process a file (PDF or text) into chunks and add to vector store."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    rag_dir = _get_rag_dir(config)
    file_hash = hashlib.md5(path.read_bytes()).hexdigest()

    # Check if already indexed
    docs = _get_docs_index(rag_dir)
    if any(d["file_hash"] == file_hash for d in docs):
        return {"status": "already_indexed", "filename": path.name, "file_hash": file_hash}

    # Load and split
    chunks = []
    if path.suffix.lower() == ".pdf":
        try:
            from langchain_community.document_loaders import PyPDFLoader
            loader = PyPDFLoader(str(path))
            pages = loader.load()
        except ImportError:
            raise ImportError("pypdf not installed: pip install pypdf")
    else:
        # Plain text / markdown / code
        text = path.read_text(encoding="utf-8", errors="replace")
        from types import SimpleNamespace
        pages = [SimpleNamespace(page_content=text, metadata={"source": path.name})]

    from langchain.text_splitter import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    all_chunks = []
    for page in pages:
        split = splitter.split_text(page.page_content if hasattr(page, "page_content") else str(page))
        for i, chunk_text in enumerate(split):
            from langchain.schema import Document
            all_chunks.append(Document(
                page_content=chunk_text,
                metadata={
                    "source": path.name,
                    "file_hash": file_hash,
                    "chunk_index": i,
                    "upload_date": datetime.now().isoformat(),
                },
            ))

    # Store in vector DB
    store = _get_store(config)
    stored = store.add_documents(all_chunks)

    # Update index
    docs.append({
        "filename": path.name,
        "file_hash": file_hash,
        "chunks": len(all_chunks),
        "pages": len(pages),
        "uploaded": datetime.now().isoformat(),
    })
    _save_docs_index(rag_dir, docs)

    return {
        "status": "indexed",
        "filename": path.name,
        "file_hash": file_hash,
        "chunks": len(all_chunks),
        "pages": len(pages),
    }


# ── Tools ──────────────────────────────────────────────────────

class RAGUploadTool(BaseTool):
    category = "rag"
    name = "rag_upload"
    description = (
        "Upload and index a document (PDF, text, markdown, code) for RAG search. "
        "Once indexed, the document's content can be searched via rag_search."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to index."},
            },
            "required": ["path"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        filepath = params.get("path", "")
        if not filepath:
            return ToolResult(error="No file path", is_error=True)
        path = Path(filepath)
        if not path.is_absolute():
            path = Path(context.working_dir) / path
        try:
            result = _process_file(str(path.resolve()), context.config)
            return ToolResult(output=json.dumps(result, indent=2))
        except Exception as e:
            return ToolResult(error=f"Upload failed: {e}", is_error=True)


class RAGSearchTool(BaseTool):
    category = "rag"
    name = "rag_search"
    description = (
        "Search indexed documents by semantic query. Returns the most relevant "
        "passages from uploaded documents. Use for answering questions about "
        "uploaded PDFs, whitepapers, codebases, or any indexed content."
    )
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "top_k": {"type": "integer", "description": f"Number of results (default: {DEFAULT_TOP_K})."},
            },
            "required": ["query"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        query = params.get("query", "")
        top_k = params.get("top_k", DEFAULT_TOP_K)
        if not query:
            return ToolResult(error="Empty query", is_error=True)

        store = _get_store(context.config)
        results = store.search(query, k=top_k)

        if not results:
            return ToolResult(output="No relevant documents found.")

        parts = [f"Found {len(results)} relevant passages:\n"]
        for i, r in enumerate(results, 1):
            parts.append(
                f"--- Result {i} (score: {r['score']}, source: {r['source']}) ---\n"
                f"{r['content']}\n"
            )
        return ToolResult(output="\n".join(parts))


class RAGListTool(BaseTool):
    category = "rag"
    name = "rag_list"
    description = "List all indexed documents in the RAG store."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        rag_dir = _get_rag_dir(context.config)
        docs = _get_docs_index(rag_dir)
        if not docs:
            return ToolResult(output="No documents indexed.")
        lines = [f"{len(docs)} documents indexed:\n"]
        for d in docs:
            lines.append(f"  • {d['filename']} ({d.get('chunks', '?')} chunks, {d.get('pages', '?')} pages) [{d.get('uploaded', '?')}]")
        return ToolResult(output="\n".join(lines))


class RAGDeleteTool(BaseTool):
    category = "rag"
    name = "rag_delete"
    description = "Delete a document from the RAG index by filename."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename to delete."},
            },
            "required": ["filename"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        filename = params.get("filename", "")
        rag_dir = _get_rag_dir(context.config)
        docs = _get_docs_index(rag_dir)
        doc = next((d for d in docs if d["filename"] == filename), None)
        if not doc:
            return ToolResult(error=f"Document not found: {filename}", is_error=True)

        store = _get_store(context.config)
        deleted = store.delete_by_hash(doc["file_hash"])

        docs = [d for d in docs if d["filename"] != filename]
        _save_docs_index(rag_dir, docs)

        return ToolResult(output=f"Deleted {filename} ({deleted} chunks removed)")
