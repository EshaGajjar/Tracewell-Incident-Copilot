import { useState } from "react";
import { api } from "../firebase.js";

// Quick-fill buttons for the demo scenarios (match the bad revisions A deploys).
const SCENARIOS = {
  "Bad DATABASE_URL": {
    title: "Payments failing with 500 errors",
    service: "payment-api",
    description: "Customers report checkout failures. /pay returns 500 since the last deploy.",
  },
  "Bad Redis config": {
    title: "Payments very slow",
    service: "payment-api",
    description: "/pay takes over 5 seconds and sometimes times out.",
  },
  "Missing env var": {
    title: "New payment-api revision not serving",
    service: "payment-api",
    description: "The latest revision fails to start and requests are erroring.",
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
  const set = (k) => (e) => setForm((previous) => ({ ...previous, [k]: e.target.value }));

  // The ticket is written to Firestore first. Copilot investigation runs only after that succeeds.
  async function submit(askCopilot) {
    if (!form.title.trim() || !form.description.trim()) {
      setError("Title and description are required.");
      return;
    }

    setBusyAction(askCopilot ? "investigate" : "create");
    setStage("Saving incident to the Tracewell store…");
    setError("");

    try {
      console.info("[Tracewell] submit started", { askCopilot });
      // Use the API for both creation and investigation so they share one data store.
      const created = await withTimeout(
        api("/incidents", {
          title: form.title.trim(),
          service: form.service.trim(),
          severity: form.severity,
          description: form.description.trim(),
        }, { timeoutMs: 20000 }),
        22000,
        "Timed out while creating the incident through the API. Check the backend terminal."
      );
      const incidentId = created.incident_id;
      console.info("[Tracewell] API created incident", incidentId);

      if (files.length) {
        setStage("Local attachment upload is not enabled yet; continuing without attachments.");
      }

      if (askCopilot) {
        setStage("Running Copilot investigation…");
        console.info("[Tracewell] calling /investigate", incidentId);
        await api("/investigate", { incident_id: incidentId }, { timeoutMs: 60000 });
        console.info("[Tracewell] investigation request completed", incidentId);
      }

      onCreated(incidentId);
    } catch (err) {
      console.error("[Tracewell] submit failed", err);
      setError(err?.message || "Could not create the incident. Check the browser console and backend logs.");
      setStage("");
    } finally {
      setBusyAction("");
    }
  }

  return (
    <div className="card">
      <div className="quick">
        {Object.entries(SCENARIOS).map(([label, scenario]) => (
          <button
            type="button"
            key={label}
            disabled={Boolean(busyAction)}
            onClick={() => setForm((previous) => ({ ...previous, ...scenario }))}
          >
            {label}
          </button>
        ))}
      </div>
      <label>
        Title
        <input value={form.title} onChange={set("title")} disabled={Boolean(busyAction)} />
      </label>
      <label>
        Service
        <input value={form.service} onChange={set("service")} disabled={Boolean(busyAction)} />
      </label>
      <label>
        Severity
        <select value={form.severity} onChange={set("severity")} disabled={Boolean(busyAction)}>
          {["SEV1", "SEV2", "SEV3", "SEV4"].map((severity) => (
            <option key={severity}>{severity}</option>
          ))}
        </select>
      </label>
      <label>
        Description
        <textarea rows={4} value={form.description} onChange={set("description")} disabled={Boolean(busyAction)} />
      </label>
      <label>
        Screenshots (optional; local upload is not enabled)
        <input
          type="file"
          accept="image/*"
          multiple
          disabled={Boolean(busyAction)}
          onChange={(event) => setFiles(Array.from(event.target.files || []))}
        />
      </label>
      <button disabled={Boolean(busyAction)} onClick={() => submit(true)}>
        {busyAction === "investigate" ? "Working…" : "Create and investigate with Copilot"}
      </button>{" "}
      <button disabled={Boolean(busyAction)} className="secondary" onClick={() => submit(false)}>
        {busyAction === "create" ? "Creating…" : "Create ticket only"}
      </button>
      {stage && <p className="muted" role="status">{stage}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
