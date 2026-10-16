"""Runtime configuration. Environment variables are deliberately explicit."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

APP_NAME = os.getenv("ADK_APP_NAME", "tracewell_incident_copilot")
_raw_project = (
    os.getenv("GCP_PROJECT")
    or os.getenv("GOOGLE_CLOUD_PROJECT")
    or os.getenv("GCLOUD_PROJECT")
    or os.getenv("GCP_PROJECT_ID")
)
GCP_PROJECT = None if not _raw_project or _raw_project.strip() in ("", "your-project-id", "your-gcp-project") else _raw_project.strip()
GCP_REGION = os.getenv("GCP_REGION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
USE_VERTEX_AI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"
CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",") if x.strip()]
LOG_LOOKBACK_MINUTES = int(os.getenv("LOG_LOOKBACK_MINUTES", "30"))
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "18"))
LOCAL_FALLBACK = os.getenv("TRACEWELL_LOCAL_FALLBACK", "true").lower() == "true"
