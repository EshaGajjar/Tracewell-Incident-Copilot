import { useState } from "react";
import IncidentList from "./components/IncidentList.jsx";
import IncidentDetail from "./components/IncidentDetail.jsx";
import ReportForm from "./components/ReportForm.jsx";
import { API_URL } from "./firebase.js";

export default function App() {
  const [view, setView] = useState({ name: "list" });
  return (
    <div className="app">
      <header>
        <h1 onClick={() => setView({ name: "list" })}>Tracewell</h1>
        <button onClick={() => setView({ name: "new" })}>Report incident</button>
      </header>
      {!API_URL && <p className="muted">Backend URL not set (VITE_API_URL): Copilot, approve, reject and close are disabled until A deploys the API.</p>}
      <main>
        {view.name === "list" && <IncidentList onOpen={(id) => setView({ name: "detail", id })} />}
        {view.name === "new" && <ReportForm onCreated={(id) => setView({ name: "detail", id })} />}
        {view.name === "detail" && <IncidentDetail id={view.id} />}
      </main>
    </div>
  );
}
