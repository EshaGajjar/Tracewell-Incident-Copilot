"""Embed knowledge-base docs and past incidents and store them in Firestore.

Usage:
  export GCP_PROJECT=your-project-id
  export GCP_REGION=us-central1
  gcloud auth application-default login
  python scripts/ingest.py
Idempotent: documents are written by id, so re-running overwrites them.
"""
import json, os, pathlib
import vertexai
from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector

MODEL, DIM = "text-embedding-005", 768
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

def _init():
    project = os.environ["GCP_PROJECT"]
    vertexai.init(project=project, location=os.environ.get("GCP_REGION", "us-central1"))
    return TextEmbeddingModel.from_pretrained(MODEL), firestore.Client(project=project)

def embed(model, texts, task="RETRIEVAL_DOCUMENT"):
    out = []
    for i in range(0, len(texts), 5):
        batch = [TextEmbeddingInput(t, task) for t in texts[i:i + 5]]
        out += [e.values for e in model.get_embeddings(batch, output_dimensionality=DIM)]
    return out

def ingest(db, model, collection, items, to_text):
    vectors = embed(model, [to_text(x) for x in items])
    for item, vec in zip(items, vectors):
        db.collection(collection).document(item["id"]).set({**item, "embedding": Vector(vec)})
    print(f"{collection}: wrote {len(items)} documents")

if __name__ == "__main__":
    model, db = _init()
    docs = json.loads((DATA / "kb_docs.json").read_text())
    incidents = json.loads((DATA / "past_incidents.json").read_text())
    ingest(db, model, "kb_docs", docs, lambda d: f"{d['title']}\n{d['content']}")
    ingest(db, model, "past_incidents", incidents,
           lambda i: f"{i['service']}: {i['symptom']}\nCause: {i['cause']}\nFix: {i['fix']}")
