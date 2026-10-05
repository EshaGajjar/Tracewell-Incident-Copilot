# Deck outline (PDF, submit with prototype)
1. Title: Tracewell Incident Copilot, category Future of Work and Enterprise Productivity
2. Problem: engineers lose time on first-level incident triage; evidence is scattered (logs, deploys, docs)
3. Solution: investigate, reason, recommend, human approval, act, verify, document, learn
4. Demo flow: report incident, live timeline, evidence, approve, rollback, verification, report
5. Architecture: dashboard (Firebase Hosting) -> FastAPI (Cloud Run) -> ADK agent (Gemini on Vertex AI) -> Firestore, Cloud Logging, Cloud Run
6. What is different: approval enforced in code, evidence-strength grading, learning loop
7. Impact: time saved per incident (fill in from the timed demo)
8. Evaluation: 15 test incidents, classification and root-cause accuracy (fill in real numbers)
9. Scalability: Pub/Sub alert triggers, BigQuery analytics, multi-service support
10. Team and next steps
