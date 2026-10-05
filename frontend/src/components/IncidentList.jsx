import { useEffect, useState } from "react";
import { collection, onSnapshot, orderBy, query } from "firebase/firestore";
import { db } from "../firebase.js";

export default function IncidentList({ onOpen }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const q = query(collection(db, "incidents"), orderBy("created_at", "desc"));
    return onSnapshot(q, (snap) => setItems(snap.docs.map((d) => ({ id: d.id, ...d.data() }))));
  }, []);
  if (!items.length) return <p className="muted">No incidents yet.</p>;
  return (
    <ul className="list">
      {items.map((i) => (
        <li key={i.id} onClick={() => onOpen(i.id)}>
          <span className={`sev ${i.severity || ""}`}>{i.severity || "—"}</span>
          <strong>{i.title}</strong>
          <span className="muted">{i.service}</span>
          <span className={`status ${i.status}`}>{i.status}</span>
        </li>
      ))}
    </ul>
  );
}
