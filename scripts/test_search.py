"""Try a vector search.  Usage: python scripts/test_search.py "500 errors after deployment" """
import sys
from ingest import _init, embed
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

query = " ".join(sys.argv[1:]) or "500 errors after deployment"
model, db = _init()
vec = embed(model, [query], task="RETRIEVAL_QUERY")[0]
for coll, label in [("kb_docs", "title"), ("past_incidents", "symptom")]:
    print(f"\n== {coll} ==")
    results = db.collection(coll).find_nearest(
        vector_field="embedding", query_vector=Vector(vec),
        distance_measure=DistanceMeasure.COSINE, limit=3).get()
    for r in results:
        print(f"- {r.id}: {r.to_dict()[label]}")
