import { useEffect, useState } from "react";
import { collection, doc, onSnapshot, orderBy, query } from "firebase/firestore";
import { db, api } from "../firebase.js";

export default function IncidentDetail({ id }) {
  const [inc, setInc] = useState(null);
  const [steps, setSteps] = useState([]);

  useEffect(() => {
    const u1 = onSnapshot(doc(db, "incidents", id), (d) => setInc({ id: d.id, ...d.data() }));
    const q = query(collection(db, "incidents", id, "steps"), orderBy("index"));
    const u2 = onSnapshot(q, (s) => setSteps(s.docs.map((d) => ({ id: d.id, ...d.data() }))));
    return () => { u1(); u2(); };
  }, [id]);

  if (!inc) return <p className="muted">Loading…</p>;
  const diag = inc.diagnosis, pending = inc.pending_action;
  const closed = inc.status === "resolved" || inc.status === "closed";

  return (
    <div className="detail">
      <h2>{inc.title} <span className={`status ${inc.status}`}>{inc.status}</span></h2>
      <p className="muted">{inc.service} · {inc.category || "unclassified"} · {inc.severity || "—"}</p>

      <section className="card">
        <h3>Problem</h3>
        <p>{inc.description}</p>
        <div className="shots">
          {(inc.attachments || []).map((a) => (
            <a key={a.url} href={a.url} target="_blank" rel="noreferrer"><img src={a.url} alt={a.name} /></a>
          ))}
        </div>
        {!closed && !steps.length && inc.source === "human" && (
          <button onClick={() => api("/investigate", { incident_id: id })}>Investigate with Copilot</button>
        )}
      </section>

      <section className="card">
        <h3>Investigation timeline</h3>
        {steps.length === 0 && <p className="muted">Waiting for the agent…</p>}
        <ol className="timeline">
          {steps.map((s) => (
            <li key={s.id}><strong>{s.tool || s.type}</strong>
              <pre>{typeof s.output === "string" ? s.output : JSON.stringify(s.output, null, 2)}</pre></li>
          ))}
        </ol>
      </section>

      {diag && (
        <section className="card">
          <h3>Diagnosis · <span className={`strength ${diag.evidence_strength}`}>{diag.evidence_strength}</span></h3>
          <p>{diag.root_cause}</p>
          <ul>{(diag.evidence || []).map((e, i) => <li key={i}><em>{e.type}</em>: {e.detail}</li>)}</ul>
        </section>
      )}

      {pending && pending.status === "pending" && (
        <section className="card">
          <h3>Recommended action</h3>
          <p><code>{pending.action}</code> — {pending.reason}</p>
          <button onClick={() => api("/approve", { incident_id: id })}>Approve</button>{" "}
          <button className="secondary" onClick={() => api("/reject", { incident_id: id })}>Reject</button>
        </section>
      )}

      {inc.verification && (
        <section className="card"><h3>Verification</h3>
          <pre>{JSON.stringify(inc.verification, null, 2)}</pre></section>
      )}
      {!closed && <CloseForm id={id} />}
      {closed && inc.resolution && (
        <section className="card"><h3>Resolution</h3>
          <p><strong>Root cause:</strong> {inc.root_cause}</p>
          <p><strong>Fix:</strong> {inc.resolution}</p></section>
      )}
      {inc.report && (
        <section className="card"><h3>Incident report</h3>
          <pre className="report">{typeof inc.report === "string" ? inc.report : JSON.stringify(inc.report, null, 2)}</pre></section>
      )}
    </div>
  );
}

function CloseForm({ id }) {
  const [rc, setRc] = useState("");
  const [fix, setFix] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // The backend (/close) saves the resolution, marks the incident closed and embeds it into past_incidents.
  async function close() {
    if (!rc || !fix) { setError("Root cause and resolution are required."); return; }
    setBusy(true); setError("");
    try { await api("/close", { incident_id: id, root_cause: rc, resolution: fix }); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return (
    <section className="card">
      <h3>Close ticket</h3>
      <label>Root cause<textarea rows={2} value={rc} onChange={(e) => setRc(e.target.value)} /></label>
      <label>Resolution<textarea rows={3} value={fix} onChange={(e) => setFix(e.target.value)} /></label>
      <button disabled={busy} onClick={close}>{busy ? "Closing…" : "Close with resolution"}</button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
