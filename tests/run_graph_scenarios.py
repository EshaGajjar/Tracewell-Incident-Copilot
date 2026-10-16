#!/usr/bin/env python3
"""Create graph-aligned Tracewell incidents, request causal graphs, and save resolutions.

Run against localhost or a dedicated staging environment only.
This does not mutate real infrastructure. Resolution narratives are simulated.
"""
from __future__ import annotations
import argparse, concurrent.futures, json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

def call(base, path, method="GET", payload=None, timeout=45):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept":"application/json"}
    if data is not None: headers["Content-Type"]="application/json"
    req=urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    started=time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw=res.read()
            return res.status, json.loads(raw) if raw else None, round((time.perf_counter()-started)*1000,2)
    except urllib.error.HTTPError as e:
        raw=e.read()
        try: body=json.loads(raw) if raw else None
        except Exception: body=raw.decode(errors="replace")
        return e.code, body, round((time.perf_counter()-started)*1000,2)
    except Exception as e:
        return 0, {"error":f"{type(e).__name__}: {e}"}, round((time.perf_counter()-started)*1000,2)

def one(base, scenario, index, args):
    stamp=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    title=f"[GRAPH-TEST-{stamp}-{index:03d}] {scenario['title']}"
    create={"title":title,"service":scenario["service"],"severity":scenario["severity"],
            "description":scenario["description"]}
    row={"scenario_id":scenario["id"],"title":title,"service":scenario["service"],"timings_ms":{},"errors":[]}
    code, body, ms=call(base,"/incidents","POST",create,args.timeout)
    row["timings_ms"]["create"]=ms
    if code not in (200,201) or not isinstance(body,dict) or not body.get("incident_id"):
        row["errors"].append({"stage":"create","status":code,"body":body}); return row
    iid=body["incident_id"]; row["incident_id"]=iid; row["created"]=True

    # Ask backend to build the graph from its configured dependency catalogue.
    graph_path=f"/incidents/{urllib.parse.quote(iid,safe='')}/causal-graph"
    if args.force_refresh:
        graph_path += "?force_refresh=true"
    code, graph, ms=call(base,graph_path,"GET",timeout=args.timeout)
    row["timings_ms"]["causal_graph"]=ms
    row["graph_status"]=code
    if code != 200 or not isinstance(graph,dict):
        row["errors"].append({"stage":"causal_graph","status":code,"body":graph})
    else:
        nodes=graph.get("nodes",[])
        edges=graph.get("edges",[])
        row["graph"]={"graph_id":graph.get("graph_id"),"node_count":len(nodes) if isinstance(nodes,list) else None,
                      "edge_count":len(edges) if isinstance(edges,list) else None,
                      "node_labels":[n.get("label") for n in nodes if isinstance(n,dict)],
                      "relationships":[e.get("relationship_type") for e in edges if isinstance(e,dict)]}
        expected={scenario["service"],*scenario.get("related_services",[])}
        observed=" ".join(row["graph"]["node_labels"] or [])
        row["expected_services"]=sorted(expected)
        row["expected_services_visible"]=all(svc in observed for svc in expected)

    if args.investigate:
        code, body, ms=call(base,"/investigate","POST",{"incident_id":iid},args.timeout)
        row["timings_ms"]["investigate"]=ms
        row["investigation_status"]=code
        if code not in (200,201): row["errors"].append({"stage":"investigate","status":code,"body":body})

    if args.resolve:
        close_payload={"incident_id":iid,"root_cause":scenario["root_cause"],"resolution":scenario["resolution"]}
        code, body, ms=call(base,"/close","POST",close_payload,args.timeout)
        row["timings_ms"]["close"]=ms; row["close_status"]=code
        if code not in (200,201): row["errors"].append({"stage":"close","status":code,"body":body})
        else:
            code2, detail, ms2=call(base,f"/incidents/{urllib.parse.quote(iid,safe='')}",timeout=args.timeout)
            row["timings_ms"]["verify_resolution"]=ms2
            row["resolution_verified"]=(code2==200 and isinstance(detail,dict)
                and detail.get("status") in ("closed","resolved")
                and bool(detail.get("root_cause")) and bool(detail.get("resolution")))
            if not row["resolution_verified"]:
                row["errors"].append({"stage":"verify_resolution","status":code2,"body":detail})
    return row

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url",default="http://127.0.0.1:8000")
    p.add_argument("--count",type=int,default=6,help="number of incidents; scenarios repeat if count exceeds catalogue")
    p.add_argument("--concurrency",type=int,default=1)
    p.add_argument("--timeout",type=float,default=45)
    p.add_argument("--allow-remote",action="store_true")
    p.add_argument("--investigate",action="store_true",help="also invoke LLM/agent investigation; may be slow/costly")
    p.add_argument("--no-resolve",dest="resolve",action="store_false",help="leave incidents open")
    p.set_defaults(resolve=True)
    p.add_argument("--force-refresh",action="store_true")
    p.add_argument("--output",default="tracewell-graph-scenario-report.json")
    p.add_argument("--scenarios",default=str(Path(__file__).with_name("scenarios.json")))
    a=p.parse_args()
    from urllib.parse import urlparse
    u=urlparse(a.base_url); local=u.hostname in ("localhost","127.0.0.1","::1")
    if u.scheme not in ("http","https") or not u.netloc: p.error("base URL must be HTTP(S)")
    if not local and not a.allow_remote: p.error("Remote target blocked; use a dedicated staging host and --allow-remote")
    if not local and any(s in (u.hostname or "").lower() for s in ("prod","production")): p.error("Production-like host refused")
    if a.count<1 or a.count>1000 or a.concurrency<1 or a.concurrency>20: p.error("count must be 1..1000 and concurrency 1..20")
    catalogue=json.loads(Path(a.scenarios).read_text())
    scenarios=catalogue["scenarios"]
    health,body,_=call(a.base_url,"/health",timeout=a.timeout)
    if health!=200: p.error(f"health preflight failed: HTTP {health} {body}")
    jobs=[(i,scenarios[i%len(scenarios)]) for i in range(a.count)]
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        futures=[pool.submit(one,a.base_url,s,i,a) for i,s in jobs]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
            print(f"Completed {len(results)}/{a.count}")
    results.sort(key=lambda x:x.get("incident_id",""))
    report={"target":a.base_url,"created_at":datetime.now(timezone.utc).isoformat(),
            "requested":a.count,"created":sum(bool(x.get("created")) for x in results),
            "graphs_ok":sum(x.get("graph_status")==200 for x in results),
            "resolutions_verified":sum(bool(x.get("resolution_verified")) for x in results),
            "failures":sum(bool(x.get("errors")) for x in results),
            "scenarios":results}
    Path(a.output).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="scenarios"},indent=2))
    print("Detailed report:",a.output)
    return 1 if report["failures"] else 0
if __name__=="__main__": raise SystemExit(main())
