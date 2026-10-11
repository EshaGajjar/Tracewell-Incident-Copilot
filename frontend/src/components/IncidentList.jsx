import { useEffect, useState } from "react";
import { request } from "../firebase.js";

function prettyStatus(value = "open") { return value.replaceAll("_", " "); }
function relativeTime(value) {
  if (!value) return "Just now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Recently";
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function IncidentList({ onOpen, onCreate }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function load() {
      try { const rows = await request("/incidents"); if (active) { setItems(rows); setError(""); } }
      catch (e) { if (active) setError(e.message || "Could not load incidents"); }
      finally { if (active) setLoading(false); }
    }
    load();
    const timer = setInterval(load, 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  const visible = items.filter((i) => {
    const matchesFilter = filter === "all" || (filter === "active" ? !["closed", "resolved", "rejected"].includes(i.status) : i.status === filter);
    const text = `${i.title || ""} ${i.service || ""} ${i.id || ""}`.toLowerCase();
    return matchesFilter && text.includes(query.toLowerCase());
  });

  return <div className="incident-section">
    <div className="section-heading"><div><div className="eyebrow">LIVE FEED</div><h2>Recent incidents <span className="count-badge">{items.length}</span></h2><p>Every signal, one place to investigate and resolve.</p></div><button className="primary-button" onClick={onCreate}>＋ New incident</button></div>
    <div className="list-toolbar"><div className="filter-tabs">
      {[ ["all", "All incidents"], ["active", "Active"], ["awaiting_approval", "Needs approval"], ["closed", "Closed"] ].map(([key, label]) => <button key={key} className={filter === key ? "filter-tab active" : "filter-tab"} onClick={() => setFilter(key)}>{label}</button>)}
    </div><label className="search-box"><span>⌕</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search incidents…" /></label></div>

    {error && <div className="notice error-notice"><strong>Could not load incidents</strong><span>{error}</span></div>}
    {loading && !items.length ? <div className="empty-state"><div className="loader-orb" /><strong>Connecting to incident store</strong><span>Loading the latest incidents…</span></div> : visible.length === 0 ? <div className="empty-state"><div className="empty-icon">⌁</div><strong>{items.length ? "No matching incidents" : "Your incident queue is clear"}</strong><span>{items.length ? "Try another search or filter." : "Report an incident to start an evidence-backed investigation."}</span>{!items.length && <button className="primary-button" onClick={onCreate}>＋ Report first incident</button>}</div> :
      <div className="incident-table-wrap"><table className="incident-table"><thead><tr><th>INCIDENT</th><th>SERVICE</th><th>SEVERITY</th><th>STATUS</th><th>REPORTED</th><th></th></tr></thead><tbody>
        {visible.map((i) => <tr key={i.id} onClick={() => onOpen(i.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(i.id)}>
          <td><div className="incident-title-cell"><span className={`incident-glyph ${i.status === "awaiting_approval" ? "amber" : i.status === "closed" || i.status === "resolved" ? "green" : "blue"}`}>{i.status === "awaiting_approval" ? "⌁" : i.status === "closed" || i.status === "resolved" ? "✓" : "↗"}</span><span><strong>{i.title || "Untitled incident"}</strong><small>{i.id}</small></span></div></td>
          <td><span className="service-tag">{i.service || "unknown service"}</span></td>
          <td><span className={`severity-pill ${(i.severity || "SEV3").toLowerCase()}`}><span />{i.severity || "SEV3"}</span></td>
          <td><span className={`status-pill ${(i.status || "open").replaceAll("_", "-")}`}><span />{prettyStatus(i.status)}</span></td>
          <td className="time-cell">{relativeTime(i.created_at)}</td><td className="row-arrow">↗</td>
        </tr>)}
      </tbody></table></div>}
    <div className="list-foot"><span><span className="pulse-dot" /> Auto-refreshing every 5 seconds</span><span>{visible.length} of {items.length} incidents</span></div>
  </div>;
}
