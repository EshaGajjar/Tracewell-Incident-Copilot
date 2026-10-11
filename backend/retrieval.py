"""Retrieval over existing data/*.json with Firestore vector search when configured.

If the cloud vector index is unavailable, the service falls back to a transparent
lexical scorer over the checked-in corpus so local development remains possible.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from .config import GCP_PROJECT, GCP_REGION
from .store import get_db

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EMBEDDING_MODEL = "text-embedding-005"
EMBEDDING_DIM = 768


def _load(name: str) -> list[dict[str, Any]]:
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _terms(text: str) -> list[str]:
    return [x for x in re.findall(r"[a-z0-9_./-]{3,}", text.lower()) if x not in {"the", "and", "for", "with", "from", "after", "what", "how", "are", "was", "were"}]


def _lexical_search(query: str, collection: str, limit: int) -> list[dict[str, Any]]:
    docs = _load("kb_docs.json") if collection == "kb_docs" else _load("past_incidents.json")
    terms = _terms(query)
    ranked = []
    for doc in docs:
        if collection == "kb_docs":
            text = f"{doc.get('title','')} {doc.get('type','')} {doc.get('service','')} {doc.get('content','')}".lower()
        else:
            text = f"{doc.get('service','')} {doc.get('category','')} {doc.get('severity','')} {doc.get('symptom','')} {doc.get('cause','')} {doc.get('fix','')}".lower()
        score = sum(min(text.count(t), 4) * (2 if t in str(doc.get("title", "")).lower() else 1) for t in terms)
        if score:
            result = {k: v for k, v in doc.items() if k != "embedding"}
            result["retrieval_score"] = score
            result["retrieval_method"] = "lexical_fallback"
            ranked.append((score, result))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in ranked[:limit]]


def _embed_query(query: str) -> list[float]:
    import vertexai
    from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
    vertexai.init(project=GCP_PROJECT, location=GCP_REGION)
    model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    return model.get_embeddings([TextEmbeddingInput(query, "RETRIEVAL_QUERY", output_dimensionality=EMBEDDING_DIM)])[0].values


def _vector_search(query: str, collection: str, limit: int) -> list[dict[str, Any]]:
    from google.cloud.firestore_v1.vector import Vector
    from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
    db = get_db()
    vector = Vector(_embed_query(query))
    snaps = db.collection(collection).find_nearest(vector_field="embedding", query_vector=vector,
                                                    distance_measure=DistanceMeasure.COSINE, limit=limit).get()
    out = []
    for snap in snaps:
        item = snap.to_dict() or {}
        item["id"] = item.get("id", snap.id)
        item.pop("embedding", None)
        item["retrieval_method"] = "firestore_vector"
        out.append(item)
    return out


def search_knowledge(query: str, limit: int = 4) -> dict[str, Any]:
    """Retrieve relevant runbooks and service documentation for an incident."""
    try:
        if GCP_PROJECT:
            hits = _vector_search(query, "kb_docs", limit)
            if hits:
                return {"query": query, "results": hits, "method": "firestore_vector"}
    except Exception as exc:
        hits = _lexical_search(query, "kb_docs", limit)
        return {"query": query, "results": hits, "method": "lexical_fallback", "warning": f"Vector search unavailable: {type(exc).__name__}"}
    return {"query": query, "results": _lexical_search(query, "kb_docs", limit), "method": "lexical_fallback"}


def search_past_incidents(query: str, limit: int = 4) -> dict[str, Any]:
    """Retrieve similar historical incidents and their known causes and fixes."""
    try:
        if GCP_PROJECT:
            hits = _vector_search(query, "past_incidents", limit)
            if hits:
                return {"query": query, "results": hits, "method": "firestore_vector"}
    except Exception as exc:
        hits = _lexical_search(query, "past_incidents", limit)
        return {"query": query, "results": hits, "method": "lexical_fallback", "warning": f"Vector search unavailable: {type(exc).__name__}"}
    return {"query": query, "results": _lexical_search(query, "past_incidents", limit), "method": "lexical_fallback"}
