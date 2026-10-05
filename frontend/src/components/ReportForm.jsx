import { useState } from "react";
import { addDoc, collection, doc, serverTimestamp, updateDoc } from "firebase/firestore";
import { db, api } from "../firebase.js";
import { uploadAttachments } from "../uploads.js";

// Quick-fill buttons for the demo scenarios (match the bad revisions A deploys).
const SCENARIOS = {
  "Bad DATABASE_URL": { title: "Payments failing with 500 errors", service: "payment-api",
    description: "Customers report checkout failures. /pay returns 500 since the last deploy." },
  "Bad Redis config": { title: "Payments very slow", service: "payment-api",
    description: "/pay takes over 5 seconds and sometimes times out." },
  "Missing env var": { title: "New payment-api revision not serving", service: "payment-api",
    description: "The latest revision fails to start and requests are erroring." },
};

export default function ReportForm({ onCreated }) {
  const [form, setForm] = useState({ title: "", service: "payment-api", severity: "SEV3", description: "" });
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  // Creates the ticket directly in Firestore (source: human). Optionally asks the Copilot to investigate.
  async function submit(askCopilot) {
    if (!form.title || !form.description) { setError("Title and description are required."); return; }
    setBusy(true); setError("");
    try {
      const ref = await addDoc(collection(db, "incidents"), {
        ...form, source: "human", status: "open", attachments: [], created_at: serverTimestamp(),
      });
      if (files.length) {
        const attachments = await uploadAttachments(ref.id, files);
        await updateDoc(doc(db, "incidents", ref.id), { attachments });
      }
      if (askCopilot) await api("/investigate", { incident_id: ref.id });
      onCreated(ref.id);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="quick">
        {Object.entries(SCENARIOS).map(([label, s]) => (
          <button type="button" key={label} onClick={() => setForm({ ...form, ...s })}>{label}</button>
        ))}
      </div>
      <label>Title<input value={form.title} onChange={set("title")} /></label>
      <label>Service<input value={form.service} onChange={set("service")} /></label>
      <label>Severity
        <select value={form.severity} onChange={set("severity")}>
          {["SEV1", "SEV2", "SEV3", "SEV4"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <label>Description<textarea rows={4} value={form.description} onChange={set("description")} /></label>
      <label>Screenshots (optional)
        <input type="file" accept="image/*" multiple onChange={(e) => setFiles([...e.target.files])} />
      </label>
      <button disabled={busy} onClick={() => submit(true)}>{busy ? "Working…" : "Create and investigate with Copilot"}</button>{" "}
      <button disabled={busy} className="secondary" onClick={() => submit(false)}>Create ticket only</button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
