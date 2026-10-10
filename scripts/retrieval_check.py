"""Day 4: check that vector search returns the right documents (A's tools depend on this).

Usage: python scripts/retrieval_check.py     (needs ingest.py + vector indexes done)
A case passes if an expected doc is in the top 3.
"""
from ingest import _init, embed
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

DOC_CASES = [
    ("payment-api returning 500 errors after the latest deployment", {"doc_06", "doc_02"}),
    ("DATABASE_URL invalid in logs", {"doc_02", "doc_06"}),
    ("payments are slow and Redis connection refused", {"doc_03", "doc_14"}),
    ("how do I roll back a Cloud Run revision", {"doc_04"}),
    ("container failed to start and listen on PORT", {"doc_07"}),
    ("what counts as a SEV2 incident", {"doc_08"}),
    ("cloud logging query for payment-api errors", {"doc_09"}),
    ("permission denied accessing secret version", {"doc_16"}),
    ("Firestore PERMISSION_DENIED missing or insufficient permissions", {"doc_15"}),
    ("p95 latency above 1000ms investigation", {"doc_17"}),
]
INCIDENT_QUERIES = [
    "malformed DATABASE_URL after deploy 500 errors",
    "wrong Redis port slow payments",
    "revision fails to start missing env var",
    "secret manager permission denied payment-api",
]

def top(db, coll, vec, n=3):
    q = db.collection(coll).find_nearest(vector_field="embedding", query_vector=Vector(vec),
                                         distance_measure=DistanceMeasure.COSINE, limit=n).get()
    return [r.to_dict() for r in q]

model, db = _init()
passed = 0
for query, expected in DOC_CASES:
    vec = embed(model, [query], task="RETRIEVAL_QUERY")[0]
    ids = [d["id"] for d in top(db, "kb_docs", vec)]
    ok = bool(expected & set(ids)); passed += ok
    print(f"{'PASS' if ok else 'FAIL'}  {query}\n      got {ids}  expected one of {sorted(expected)}")
print(f"\nkb_docs retrieval: {passed}/{len(DOC_CASES)} passed")

print("\nPast incidents (review by eye: are these the right incidents?)")
for query in INCIDENT_QUERIES:
    vec = embed(model, [query], task="RETRIEVAL_QUERY")[0]
    print(f"\n> {query}")
    for d in top(db, "past_incidents", vec):
        print(f"  - {d['id']}: {d['symptom']}  [cause: {d['cause']}]")
