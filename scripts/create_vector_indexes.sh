#!/usr/bin/env bash
# Run once, AFTER ingest.py. Index build can take a few minutes.
set -e
for COLL in kb_docs past_incidents; do
  gcloud firestore indexes composite create \
    --collection-group="$COLL" \
    --query-scope=COLLECTION \
    --field-config=vector-config='{"dimension":"768","flat": "{}"}',field-path=embedding
done
gcloud firestore indexes composite list
