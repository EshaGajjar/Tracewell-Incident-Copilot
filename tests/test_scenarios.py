import json
from pathlib import Path

scenarios_path = Path(__file__).parent / "scenarios.json"
if not scenarios_path.exists():
    scenarios_path = Path(__file__).parents[1] / "scenarios.json"
DATA = json.loads(scenarios_path.read_text())

def test_scenarios_only_reference_known_topology_services():
    known = set(DATA["topology_services"])
    assert len(known) >= 8
    for scenario in DATA["scenarios"]:
        assert scenario["service"] in known
        assert set(scenario["related_services"]).issubset(known)

def test_every_scenario_has_resolution_and_events():
    for scenario in DATA["scenarios"]:
        assert scenario["events"]
        assert len(scenario["root_cause"]) > 20
        assert len(scenario["resolution"]) > 50
        assert scenario["title"].startswith(f"[{scenario['id']}]")

def test_ambiguous_scenario_does_not_claim_confirmed_causality():
    scenario = next(s for s in DATA["scenarios"] if s["id"] == "GRAPH-01")
    assert "insufficient evidence" in scenario["root_cause"].lower()
    assert "timing alone" in scenario["resolution"].lower()
