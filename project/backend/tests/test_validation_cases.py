"""Validation cases required by the partial rubric."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from agent import applicable, initial_state, solve, transition  # noqa: E402


def _base_scenario() -> dict:
    return {
        "robot": {
            "start": "S",
            "battery_max": 100,
            "battery_start": 100,
            "cargo_capacity": 3,
        },
        "zones": [
            {"id": "S", "name": "S", "recharge": False},
            {"id": "A", "name": "A", "recharge": False},
            {"id": "B", "name": "B", "recharge": False},
            {"id": "C", "name": "C", "recharge": False},
            {"id": "G", "name": "G", "recharge": False},
        ],
        "corridors": [],
        "doors": [],
        "keys": [],
        "tools": [],
        "materials": [],
        "panels": [],
        "stations": [],
        "chargers": [],
        "goal": {"stations_online": []},
        "action_costs": {
            "pickup": 1,
            "drop": 1,
            "interact": 1,
            "recharge": 3,
        },
    }


def _add_station_goal(scenario: dict, zone: str = "G") -> None:
    scenario["stations"] = [
        {
            "id": "TARGET",
            "kind": "test",
            "zone": zone,
            "state": "OFFLINE",
            "requires": {},
        }
    ]
    scenario["goal"] = {"stations_online": ["TARGET"]}


def test_case_1_equivalent_histories_produce_same_state() -> None:
    """Two action orders that change independent resources end in one logical state."""
    scenario = _base_scenario()
    scenario["tools"] = [
        {"id": "TOOL", "repairs": "TEST", "zone": "S", "weight": 1}
    ]
    scenario["materials"] = [
        {"type": "MAT", "zone": "S", "count": 1, "weight": 1}
    ]
    scenario["panels"] = [
        {
            "id": "PANEL",
            "zone": "G",
            "damage": "TEST",
            "requires": {"tool": "TOOL", "material": "MAT"},
            "state": "DAMAGED",
        }
    ]

    start = initial_state(scenario)

    # History 1: tool, then material.
    h1 = transition(scenario, start, ("PICKUP", "TOOL", 1))
    h1 = transition(scenario, h1, ("PICKUP", "MAT", 1))

    # History 2: material, then tool.
    h2 = transition(scenario, start, ("PICKUP", "MAT", 1))
    h2 = transition(scenario, h2, ("PICKUP", "TOOL", 1))

    assert h1 == h2
    assert hash(h1) == hash(h2)


def test_case_2_relevant_information_keeps_states_different() -> None:
    """Changing battery changes Applicable, so the states must remain different."""
    scenario = _base_scenario()
    scenario["corridors"] = [
        {"from": "S", "to": "G", "cost": 4, "door": None},
        {"from": "G", "to": "S", "cost": 4, "door": None},
    ]

    start = initial_state(scenario)
    enough = start
    low = type(start)(
        zone=start.zone,
        battery=3,
        keys=start.keys,
        tools=start.tools,
        materials=start.materials,
        ground_keys=start.ground_keys,
        ground_tools=start.ground_tools,
        ground_materials=start.ground_materials,
        doors_open=start.doors_open,
        panels_ok=start.panels_ok,
        stations_online=start.stations_online,
    )

    assert enough != low
    assert ("MOVE", "G", 4) in applicable(scenario, enough)
    assert ("MOVE", "G", 4) not in applicable(scenario, low)


def test_case_3_ucs_prefers_lower_cost_even_with_more_steps() -> None:
    """The cheaper route has more MOVE actions than the expensive route."""
    scenario = _base_scenario()
    _add_station_goal(scenario)
    scenario["corridors"] = [
        {"from": "S", "to": "A", "cost": 10, "door": None},
        {"from": "A", "to": "G", "cost": 10, "door": None},
        {"from": "S", "to": "B", "cost": 2, "door": None},
        {"from": "B", "to": "C", "cost": 2, "door": None},
        {"from": "C", "to": "G", "cost": 2, "door": None},
    ]

    result = solve(scenario)

    assert result["solution_found"] is True
    assert result["total_cost"] == 7  # 2 + 2 + 2 + ACTIVATE(1)
    moves = [step for step in result["steps"] if step["op"] == "MOVE"]
    assert [step["to"] for step in moves] == ["B", "C", "G"]


def test_case_5_alternative_routes_keep_the_cheapest_arrival() -> None:
    """Two routes reach the same world; UCS must preserve the cheaper one."""
    scenario = _base_scenario()
    _add_station_goal(scenario)
    scenario["corridors"] = [
        {"from": "S", "to": "A", "cost": 4, "door": None},
        {"from": "A", "to": "G", "cost": 4, "door": None},
        {"from": "S", "to": "B", "cost": 1, "door": None},
        {"from": "B", "to": "G", "cost": 1, "door": None},
    ]

    result = solve(scenario)

    assert result["solution_found"] is True
    assert result["total_cost"] == 3  # 1 + 1 + ACTIVATE(1)
    moves = [step for step in result["steps"] if step["op"] == "MOVE"]
    assert [step["to"] for step in moves] == ["B", "G"]