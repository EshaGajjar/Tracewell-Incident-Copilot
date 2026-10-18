import { useState } from "react";
import { api } from "../firebase.js";
import { PlusIcon, SearchIcon, AlertCircleIcon } from "./Icons.jsx";

const SCENARIOS = {
  "Bad DATABASE_URL": {
    title: "Payments failing with 500 errors",
    service: "payment-api",
    description: "Customers report checkout failures. /pay returns 500 since the last deploy.",
    severity: "SEV1",
  },
  "Bad Redis config": {
    title: "Payments experiencing high latency",
    service: "payment-api",
    description: "/pay takes over 5 seconds and sometimes times out due to Redis cache unreachable.",
    severity: "SEV2",
  },
  "Missing env var": {
    title: "Auth service failing container startup",
    service: "auth-service",
    description: "The latest revision fails to start and requests are erroring due to missing JWT secret env var.",
    severity: "SEV2",
  },
};

function withTimeout(promise, ms, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(message)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timeoutId));
}

export default function ReportForm({ onCreated }) {
  const [form, setForm] = useState({
    title: "",
    service: "payment-api",
    severity: "SEV3",
    description: "",
  });
  const [files, setFiles] = useState([]);
  const [busyAction, setBusyAction] = useState("");
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");

  const set = (k) => (e) => setForm((prev) => ({ ...prev, [k]: e.target.value }));

  async function submit(askCopilot) {
    if (!form.title.trim() || !form.description.trim()) {
      setError("Title and description are required.");
      return;
    }

    setBusyAction(askCopilot ? "investigate" : "create");
    setStage("Saving incident to Tracewell store…");
    setError("");

    try {
      setStage("Reading attached logs and saving incident evidence…");
      if (files.length > 5) throw new Error("Attach no more than 5 files per incident.");
      const attachments = await Promise.all(files.map(async (file) => {
        const allowed = [".txt", ".log", ".json", ".csv"];
        const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!allowed.includes(extension)) {
          throw new Error(`${file.name}: attach .txt, .log, .json, or .csv files. Image attachments are not yet analyzed.`);
        }
        if (file.size > 1024 * 1024) {
          throw new Error(`${file.name}: each attachment must be 1 MB or smaller.`);
        }
        const content = await file.text();
        if (content.length > 12000) {
          throw new Error(`${file.name}: extracted text must be 12,000 characters or fewer.`);
        }
        return {
          filename: file.name,
          content_type: file.type || "text/plain",
          content,
        };
      }));
      const totalAttachmentChars = attachments.reduce((sum, file) => sum + file.content.length, 0);
      if (totalAttachmentChars > 30000) {
        throw new Error("Combined text attachments must contain 30,000 characters or fewer.");
      }
      const created = await withTimeout(
        api("/incidents", {
          title: form.title.trim(),
          service: form.service.trim(),
          severity: form.severity,
          description: form.description.trim(),
          attachments,
        }, { timeoutMs: 45000 }),
        22000,
        "Timed out while creating the incident. Check the backend server."
      );
      const incidentId = created.incident_id;
      const evidence = created.evidence_collection;
      if (evidence?.status === "error") {
        setStage(`Incident saved. Evidence collection failed: ${evidence.message || "check backend logs"}`);
      } else {
        setStage(`Incident saved with ${evidence?.events_persisted ?? 0} evidence events. Opening incident…`);
      }

      onCreated(incidentId, { autoInvestigate: askCopilot, evidence });
    } catch (err) {
      setError(err?.message || "Could not create the incident.");
      setStage("");
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className="card" style={{ maxWidth: "720px" }}>
      <div style={{ marginBottom: "16px" }}>
        <span className="eyebrow" style={{ marginBottom: "6px" }}>QUICK TEMPLATES</span>
        <div className="quick-scenarios">
          {Object.entries(SCENARIOS).map(([label, sc]) => (
            <button
              type="button"
              key={label}
              className="scenario-chip"
              disabled={Boolean(busyAction)}
              onClick={() => setForm((prev) => ({ ...prev, ...sc }))}
            >
              + {label}
            </button>
          ))}
        </div>
      </div>

      <div className="form-grid">
        <div className="form-group">
          <label>Incident Title *</label>
          <input
            value={form.title}
            onChange={set("title")}
            placeholder="e.g. Elevated 500 error rates on payment checkout"
            disabled={Boolean(busyAction)}
          />
        </div>

        <div className="form-row-2">
          <div className="form-group">
            <label>Affected Service *</label>
            <input
              className="text-input"
              value={form.service}
              onChange={set("service")}
              placeholder="e.g. payment-api"
              disabled={Boolean(busyAction)}
            />
          </div>

          <div className="form-group">
            <label>Severity Level</label>
            <select
              className="select-input"
              value={form.severity}
              onChange={set("severity")}
              disabled={Boolean(busyAction)}
            >
              <option value="SEV1">SEV1 (Critical Outage)</option>
              <option value="SEV2">SEV2 (Major Degradation)</option>
              <option value="SEV3">SEV3 (Moderate Issue)</option>
              <option value="SEV4">SEV4 (Minor / Low)</option>
            </select>
          </div>
        </div>

        <div className="form-group">
          <label>Incident Description & Symptoms *</label>
          <textarea
            rows={4}
            value={form.description}
            onChange={set("description")}
            placeholder="Describe observed errors, timing, customer impact, or related deployment revisions…"
            disabled={Boolean(busyAction)}
          />
        </div>

        <div className="form-group">
          <label>Logs / Text Evidence (Optional)</label>
          <p className="muted" style={{ marginTop: "-4px", fontSize: "12px" }}>Attach .txt, .log, .json, or .csv files (max 1 MB each). Text is passed into the investigation as evidence; screenshots are not analyzed yet.</p>
          <input
            type="file"
            accept=".txt,.log,.json,.csv,text/plain,application/json,text/csv"
            multiple
            disabled={Boolean(busyAction)}
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
          />
        </div>

        <div style={{ display: "flex", gap: "10px", marginTop: "8px" }}>
          <button
            type="button"
            className="primary-button"
            disabled={Boolean(busyAction)}
            onClick={() => submit(true)}
          >
            <SearchIcon size={14} />
            <span>
              {busyAction === "investigate"
                ? "Investigating with Copilot…"
                : "Create & Investigate with Copilot"}
            </span>
          </button>

          <button
            type="button"
            className="secondary-button"
            disabled={Boolean(busyAction)}
            onClick={() => submit(false)}
          >
            <PlusIcon size={14} />
            <span>{busyAction === "create" ? "Creating…" : "Create Ticket Only"}</span>
          </button>
        </div>

        {stage && <p className="muted" role="status" style={{ marginTop: "6px" }}>{stage}</p>}
        {error && (
          <div className="notice error-notice" role="alert" style={{ marginTop: "8px" }}>
            <AlertCircleIcon size={15} />
            <span>{error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
