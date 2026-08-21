"""UCS Graph-Search agent for Emergency Control.

The search model is deliberately independent from the frontend contract.
Internal actions are translated to MOVE/PICKUP/DROP/INTERACT only at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from itertools import count
from typing import Any, Iterable


@dataclass(frozen=True)
class State:
    zone: str
    battery: int
    keys: tuple[str, ...]
    tools: tuple[str, ...]
    materials: tuple[tuple[str, int], ...]
    ground_keys: tuple[tuple[str, str], ...]
    ground_tools: tuple[tuple[str, str], ...]
    ground_materials: tuple[tuple[str, str, int], ...]
    doors_open: tuple[str, ...]
    panels_ok: tuple[str, ...]
    stations_online: tuple[str, ...]

    def world_signature(self) -> tuple[Any, ...]:
        """World configuration without battery, for resource dominance."""
        return (
            self.zone,
            self.keys,
            self.tools,
            self.materials,
            self.ground_keys,
            self.ground_tools,
            self.ground_materials,
            self.doors_open,
            self.panels_ok,
            self.stations_online,
        )


@dataclass
class Node:
    state: State
    g: int
    parent: "Node | None"
    action: tuple[Any, ...] | None


def _sorted_dict_items(d: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(d.items()))


def _material_tuple(d: dict[str, tuple[str, int]]) -> tuple[tuple[str, str, int], ...]:
    return tuple(sorted((typ, zone, count) for typ, (zone, count) in d.items() if count > 0))


def _inv_material_tuple(d: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((typ, count) for typ, count in d.items() if count > 0))


def initial_state(scenario: dict[str, Any]) -> State:
    raw = State(
        zone=scenario["robot"]["start"],
        battery=int(scenario["robot"]["battery_start"]),
        keys=(),
        tools=(),
        materials=(),
        ground_keys=tuple(sorted((k["id"], k["zone"]) for k in scenario.get("keys", []))),
        ground_tools=tuple(sorted((t["id"], t["zone"]) for t in scenario.get("tools", []))),
        ground_materials=tuple(
            sorted((m["type"], m["zone"], int(m.get("count", 0)))
                   for m in scenario.get("materials", []) if int(m.get("count", 0)) > 0)
        ),
        doors_open=tuple(sorted(d["id"] for d in scenario.get("doors", []) if d.get("state") == "OPEN")),
        panels_ok=tuple(sorted(p["id"] for p in scenario.get("panels", []) if p.get("state") == "OK")),
        stations_online=tuple(
            sorted(s["id"] for s in scenario.get("stations", []) if s.get("state") == "ONLINE")
        ),
    )
    return normalize_state(scenario, raw)


def _key_map(s: State) -> dict[str, str]:
    return dict(s.ground_keys)


def _tool_map(s: State) -> dict[str, str]:
    return dict(s.ground_tools)


def _material_ground_map(s: State) -> dict[str, tuple[str, int]]:
    return {typ: (zone, count) for typ, zone, count in s.ground_materials}


def _material_inventory(s: State) -> dict[str, int]:
    return dict(s.materials)


def _key_weight(scenario: dict[str, Any], key_id: str) -> int:
    return int(next(k for k in scenario["keys"] if k["id"] == key_id).get("weight", 1))


def _tool_weight(scenario: dict[str, Any], tool_id: str) -> int:
    return int(next(t for t in scenario["tools"] if t["id"] == tool_id).get("weight", 1))


def _material_weight(scenario: dict[str, Any], typ: str) -> int:
    return int(next(m for m in scenario["materials"] if m["type"] == typ).get("weight", 1))


def payload_weight(scenario: dict[str, Any], s: State) -> int:
    return (
        sum(_key_weight(scenario, k) for k in s.keys)
        + sum(_tool_weight(scenario, t) for t in s.tools)
        + sum(_material_weight(scenario, typ) * n for typ, n in s.materials)
    )


def _costs(scenario: dict[str, Any]) -> dict[str, int]:
    c = scenario.get("action_costs", {})
    return {
        "pickup": int(c.get("pickup", 1)),
        "drop": int(c.get("drop", 1)),
        "interact": int(c.get("interact", 2)),
        "recharge": int(c.get("recharge", 3)),
    }


def _corridor(scenario: dict[str, Any], a: str, b: str) -> dict[str, Any] | None:
    return next((c for c in scenario.get("corridors", [])
                 if c["from"] == a and c["to"] == b), None)


def _door(scenario: dict[str, Any], door_id: str) -> dict[str, Any]:
    return next(d for d in scenario["doors"] if d["id"] == door_id)


def _panel(scenario: dict[str, Any], panel_id: str) -> dict[str, Any]:
    return next(p for p in scenario["panels"] if p["id"] == panel_id)


def _station(scenario: dict[str, Any], station_id: str) -> dict[str, Any]:
    return next(s for s in scenario["stations"] if s["id"] == station_id)


def _remaining_material_demand(scenario: dict[str, Any], s: State) -> dict[str, int]:
    demand: dict[str, int] = {}
    repaired = set(s.panels_ok)
    for p in scenario.get("panels", []):
        if p["id"] not in repaired:
            typ = p["requires"]["material"]
            demand[typ] = demand.get(typ, 0) + 1
    inv = _material_inventory(s)
    for typ, amount in inv.items():
        demand[typ] = max(0, demand.get(typ, 0) - amount)
    return demand


def _is_relevant_key(scenario: dict[str, Any], s: State, key_id: str) -> bool:
    return any(d["key"] == key_id and d["id"] not in s.doors_open for d in scenario.get("doors", []))


def _is_relevant_tool(scenario: dict[str, Any], s: State, tool_id: str) -> bool:
    return any(
        p["requires"]["tool"] == tool_id and p["id"] not in s.panels_ok
        for p in scenario.get("panels", [])
    )


def _is_relevant_material(scenario: dict[str, Any], s: State, typ: str) -> bool:
    return _remaining_material_demand(scenario, s).get(typ, 0) > 0


def _inventory_has_key(s: State, key_id: str) -> bool:
    return key_id in s.keys


def _inventory_has_tool(s: State, tool_id: str) -> bool:
    return tool_id in s.tools


def _inventory_has_material(s: State, typ: str) -> bool:
    return dict(s.materials).get(typ, 0) > 0


def _replace_key_ground(s: State, values: dict[str, str]) -> State:
    return State(
        s.zone, s.battery, s.keys, s.tools, s.materials,
        _sorted_dict_items(values), s.ground_tools, s.ground_materials,
        s.doors_open, s.panels_ok, s.stations_online,
    )


def _replace_tool_ground(s: State, values: dict[str, str]) -> State:
    return State(
        s.zone, s.battery, s.keys, s.tools, s.materials,
        s.ground_keys, _sorted_dict_items(values), s.ground_materials,
        s.doors_open, s.panels_ok, s.stations_online,
    )


def _replace_materials_ground(s: State, values: dict[str, tuple[str, int]]) -> State:
    return State(
        s.zone, s.battery, s.keys, s.tools, s.materials,
        s.ground_keys, s.ground_tools, _material_tuple(values),
        s.doors_open, s.panels_ok, s.stations_online,
    )


def _with(
    s: State,
    *,
    zone: str | None = None,
    battery: int | None = None,
    keys: Iterable[str] | None = None,
    tools: Iterable[str] | None = None,
    materials: dict[str, int] | None = None,
    ground_keys: dict[str, str] | None = None,
    ground_tools: dict[str, str] | None = None,
    ground_materials: dict[str, tuple[str, int]] | None = None,
    doors_open: Iterable[str] | None = None,
    panels_ok: Iterable[str] | None = None,
    stations_online: Iterable[str] | None = None,
) -> State:
    return State(
        s.zone if zone is None else zone,
        s.battery if battery is None else battery,
        tuple(sorted(s.keys if keys is None else keys)),
        tuple(sorted(s.tools if tools is None else tools)),
        s.materials if materials is None else _inv_material_tuple(materials),
        s.ground_keys if ground_keys is None else _sorted_dict_items(ground_keys),
        s.ground_tools if ground_tools is None else _sorted_dict_items(ground_tools),
        s.ground_materials if ground_materials is None else _material_tuple(ground_materials),
        tuple(sorted(s.doors_open if doors_open is None else doors_open)),
        tuple(sorted(s.panels_ok if panels_ok is None else panels_ok)),
        tuple(sorted(s.stations_online if stations_online is None else stations_online)),
    )



def normalize_state(scenario: dict[str, Any], s: State) -> State:
    """Remove ground objects that can no longer affect any future action.

    They remain physically irrelevant to the planner: Applicable never picks
    them again, so their exact location need not enlarge the search state.
    """
    gk = {k: z for k, z in s.ground_keys if _is_relevant_key(scenario, s, k)}
    gt = {t: z for t, z in s.ground_tools if _is_relevant_tool(scenario, s, t)}
    demand = _remaining_material_demand(scenario, s)
    gm = {
        typ: (z, min(count, demand.get(typ, 0)))
        for typ, z, count in s.ground_materials
        if count > 0 and demand.get(typ, 0) > 0
    }
    return _with(s, ground_keys=gk, ground_tools=gt, ground_materials=gm)


def applicable(scenario: dict[str, Any], s: State) -> list[tuple[Any, ...]]:
    """Generate only useful successors; this is the agent's Applicable(s)."""
    c = _costs(scenario)
    actions: list[tuple[Any, ...]] = []

    # MOVE
    for corridor in scenario.get("corridors", []):
        if corridor["from"] != s.zone:
            continue
        cost = int(corridor["cost"])
        if s.battery < cost:
            continue
        door_id = corridor.get("door")
        if door_id and door_id not in s.doors_open:
            continue
        actions.append(("MOVE", corridor["to"], cost))

    # PICKUP: keys/tools/materials only if they can still contribute.
    cap = int(scenario["robot"]["cargo_capacity"])
    current_weight = payload_weight(scenario, s)

    for item, zone in s.ground_keys:
        if zone == s.zone and _is_relevant_key(scenario, s, item):
            w = _key_weight(scenario, item)
            if current_weight + w <= cap and s.battery >= c["pickup"]:
                actions.append(("PICKUP", item, c["pickup"]))

    for item, zone in s.ground_tools:
        if zone == s.zone and _is_relevant_tool(scenario, s, item):
            w = _tool_weight(scenario, item)
            if current_weight + w <= cap and s.battery >= c["pickup"]:
                actions.append(("PICKUP", item, c["pickup"]))

    demand = _remaining_material_demand(scenario, s)
    inv_mats = _material_inventory(s)
    for typ, zone, count in s.ground_materials:
        if zone == s.zone and count > 0 and demand.get(typ, 0) > 0:
            # Never collect more than the currently pending repair demand.
            if current_weight + _material_weight(scenario, typ) <= cap and s.battery >= c["pickup"]:
                actions.append(("PICKUP", typ, c["pickup"]))

    # DROP only when a useful pickup at this zone is blocked by capacity.
    useful_ground: list[tuple[str, int]] = []
    for item, zone in s.ground_keys:
        if zone == s.zone and _is_relevant_key(scenario, s, item):
            useful_ground.append((item, _key_weight(scenario, item)))
    for item, zone in s.ground_tools:
        if zone == s.zone and _is_relevant_tool(scenario, s, item):
            useful_ground.append((item, _tool_weight(scenario, item)))
    for typ, zone, count in s.ground_materials:
        if zone == s.zone and count > 0 and demand.get(typ, 0) > 0:
            useful_ground.append((typ, _material_weight(scenario, typ)))

    blocked = [(name, weight) for name, weight in useful_ground if current_weight + weight > cap]
    if blocked and s.battery >= c["drop"]:
        required_free = min(weight for _, weight in blocked)
        # Any inventory item that can free enough capacity is a legitimate
        # decision; items that free less than required are not successors.
        candidates: list[tuple[str, int, bool]] = []
        for key in s.keys:
            w = _key_weight(scenario, key)
            candidates.append((key, w, not _is_relevant_key(scenario, s, key)))
        for tool in s.tools:
            w = _tool_weight(scenario, tool)
            candidates.append((tool, w, not _is_relevant_tool(scenario, s, tool)))
        for typ, amount in s.materials:
            if amount > 0:
                w = _material_weight(scenario, typ)
                candidates.append((typ, w, not _is_relevant_material(scenario, s, typ)))

        # If an inventory object has become permanently useless, an optimal
        # plan never needs to carry it farther just to drop a different useful
        # object. Prefer these safe releases first; only when none exists do
        # we retain alternatives among still-useful resources.
        safe = [x for x in candidates if x[2] and x[1] >= required_free]
        pool = safe if safe else [x for x in candidates if x[1] >= required_free]
        for name, _weight, _irrelevant in pool:
            actions.append(("DROP", name, c["drop"]))

    # OPEN_DOOR
    if s.battery >= c["interact"]:
        for d in scenario.get("doors", []):
            if d["id"] in s.doors_open:
                continue
            if s.zone in d["between"] and _inventory_has_key(s, d["key"]):
                actions.append(("OPEN_DOOR", d["id"], c["interact"]))

    # REPAIR
    if s.battery >= c["interact"]:
        for p in scenario.get("panels", []):
            if p["id"] in s.panels_ok or p["zone"] != s.zone:
                continue
            req = p["requires"]
            if _inventory_has_tool(s, req["tool"]) and _inventory_has_material(s, req["material"]):
                actions.append(("REPAIR", p["id"], req["material"], c["interact"]))

    # ACTIVATE
    if s.battery >= c["interact"]:
        online = set(s.stations_online)
        repaired = set(s.panels_ok)
        for st in scenario.get("stations", []):
            if st["id"] in online or st["zone"] != s.zone:
                continue
            req = st.get("requires", {})
            if not set(req.get("panels_ok", [])).issubset(repaired):
                continue
            if not set(req.get("stations_online", [])).issubset(online):
                continue
            actions.append(("ACTIVATE", st["id"], c["interact"]))

    # RECHARGE. The cost is paid before battery becomes full.
    if s.battery < int(scenario["robot"]["battery_max"]) and s.battery >= c["recharge"]:
        charger_zones = {ch["zone"] for ch in scenario.get("chargers", [])}
        if s.zone in charger_zones:
            for ch in scenario.get("chargers", []):
                if ch["zone"] == s.zone:
                    actions.append(("RECHARGE", ch["id"], c["recharge"]))

    return actions


def _transition_raw(scenario: dict[str, Any], s: State, action: tuple[Any, ...]) -> State:
    kind = action[0]
    cost = int(action[-1])
    if s.battery < cost:
        raise ValueError("battery insufficient")

    if kind == "MOVE":
        _, destination, _ = action
        return _with(s, zone=destination, battery=s.battery - cost)

    if kind == "PICKUP":
        _, item, _ = action
        battery = s.battery - cost
        keys = list(s.keys)
        tools = list(s.tools)
        mats = _material_inventory(s)
        gk, gt, gm = _key_map(s), _tool_map(s), _material_ground_map(s)

        if item in gk:
            del gk[item]
            keys.append(item)
        elif item in gt:
            del gt[item]
            tools.append(item)
        elif item in gm:
            zone, count = gm[item]
            if count <= 1:
                del gm[item]
            else:
                gm[item] = (zone, count - 1)
            mats[item] = mats.get(item, 0) + 1
        else:
            raise ValueError(f"pickup item not found: {item}")

        return _with(
            s, battery=battery, keys=keys, tools=tools, materials=mats,
            ground_keys=gk, ground_tools=gt, ground_materials=gm
        )

    if kind == "DROP":
        _, item, _ = action
        battery = s.battery - cost
        keys = list(s.keys)
        tools = list(s.tools)
        mats = _material_inventory(s)
        gk, gt, gm = _key_map(s), _tool_map(s), _material_ground_map(s)

        if item in keys:
            keys.remove(item)
            gk[item] = s.zone
        elif item in tools:
            tools.remove(item)
            gt[item] = s.zone
        elif mats.get(item, 0) > 0:
            mats[item] -= 1
            if mats[item] == 0:
                del mats[item]
            if item in gm and gm[item][0] == s.zone:
                gm[item] = (s.zone, gm[item][1] + 1)
            else:
                gm[item] = (s.zone, 1)
        else:
            raise ValueError(f"drop item not in inventory: {item}")

        return _with(
            s, battery=battery, keys=keys, tools=tools, materials=mats,
            ground_keys=gk, ground_tools=gt, ground_materials=gm
        )

    if kind == "OPEN_DOOR":
        _, door_id, _ = action
        return _with(
            s, battery=s.battery - cost,
            doors_open=set(s.doors_open) | {door_id}
        )

    if kind == "REPAIR":
        _, panel_id, material, _ = action
        mats = _material_inventory(s)
        mats[material] -= 1
        if mats[material] == 0:
            del mats[material]
        return _with(
            s, battery=s.battery - cost, materials=mats,
            panels_ok=set(s.panels_ok) | {panel_id}
        )

    if kind == "ACTIVATE":
        _, station_id, _ = action
        return _with(
            s, battery=s.battery - cost,
            stations_online=set(s.stations_online) | {station_id}
        )

    if kind == "RECHARGE":
        _, _charger_id, _ = action
        return _with(s, battery=int(scenario["robot"]["battery_max"]))

    raise ValueError(f"unknown action {kind}")



def transition(scenario: dict[str, Any], s: State, action: tuple[Any, ...]) -> State:
    return normalize_state(scenario, _transition_raw(scenario, s, action))

def is_goal(scenario: dict[str, Any], s: State) -> bool:
    return set(scenario["goal"]["stations_online"]).issubset(set(s.stations_online))


def _dominates(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] >= b[0] and a[1] <= b[1]


def _reconstruct(node: Node) -> list[tuple[Any, ...]]:
    actions: list[tuple[Any, ...]] = []
    while node.parent is not None:
        assert node.action is not None
        actions.append(node.action)
        node = node.parent
    actions.reverse()
    return actions


def _action_to_plan(scenario: dict[str, Any], action: tuple[Any, ...], state: State) -> dict[str, Any]:
    kind = action[0]
    cost = int(action[-1])
    if kind == "MOVE":
        return {"op": "MOVE", "from": state.zone, "to": action[1], "cost": cost}
    if kind == "PICKUP":
        return {"op": "PICKUP", "item": action[1], "cost": cost}
    if kind == "DROP":
        return {"op": "DROP", "item": action[1], "cost": cost}
    if kind == "OPEN_DOOR":
        return {"op": "INTERACT", "target": action[1], "action": "OPEN_DOOR", "cost": cost}
    if kind == "REPAIR":
        return {
            "op": "INTERACT", "target": action[1], "action": "REPAIR",
            "consumes": action[2], "cost": cost
        }
    if kind == "ACTIVATE":
        return {"op": "INTERACT", "target": action[1], "action": "ACTIVATE", "cost": cost}
    if kind == "RECHARGE":
        return {"op": "INTERACT", "target": action[1], "action": "RECHARGE", "cost": cost}
    raise ValueError(kind)


def solve(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return an optimal-cost plan using UCS Graph Search."""
    start = initial_state(scenario)
    root = Node(start, 0, None, None)
    frontier: list[tuple[int, int, Node]] = []
    seq = count()
    heapq.heappush(frontier, (0, next(seq), root))

    # Best exact cost seen for an identical physical state.
    best_g: dict[State, int] = {start: 0}

    # Pareto frontier for each world signature: (battery, g).
    pareto: dict[tuple[Any, ...], list[tuple[int, int]]] = {
        start.world_signature(): [(start.battery, 0)]
    }

    expanded = 0

    while frontier:
        g, _, node = heapq.heappop(frontier)
        s = node.state

        if g != best_g.get(s):
            continue

        # A later-arriving node can be dominated by a cheaper/higher-battery
        # representative of the same world.
        pairs = pareto.get(s.world_signature(), [])
        if any((b, pg) != (s.battery, g) and _dominates((b, pg), (s.battery, g))
               for b, pg in pairs):
            continue

        if is_goal(scenario, s):
            internal = _reconstruct(node)
            states = []
            cur = start
            for act in internal:
                states.append(cur)
                cur = transition(scenario, cur, act)
            steps = [_action_to_plan(scenario, act, st) for act, st in zip(internal, states)]
            return {
                "solution_found": True,
                "total_cost": g,
                "steps": steps,
                "message": f"UCS Graph Search; expanded {expanded} nodes.",
            }

        expanded += 1

        for action in applicable(scenario, s):
            # Immediate inverse moves/pickup-drop pairs cannot improve a
            # positive-cost solution: no world-changing action occurs between
            # them. Avoid generating these two-step detours.
            if node.action is not None:
                prev = node.action
                if prev[0] == "MOVE" and action[0] == "MOVE":
                    # prev = MOVE(dest), so its origin is node.parent.state.zone.
                    if node.parent is not None and action[1] == node.parent.state.zone:
                        continue
                if prev[0] == "PICKUP" and action[0] == "DROP" and prev[1] == action[1]:
                    continue
                if prev[0] == "DROP" and action[0] == "PICKUP" and prev[1] == action[1]:
                    continue

            child_state = transition(scenario, s, action)
            child_g = g + int(action[-1])

            if child_g >= best_g.get(child_state, 10**18):
                continue

            sig = child_state.world_signature()
            frontier_pairs = pareto.setdefault(sig, [])
            if any(_dominates((b, pg), (child_state.battery, child_g))
                   for b, pg in frontier_pairs):
                continue

            # Remove old Pareto points dominated by this child.
            frontier_pairs[:] = [
                (b, pg) for b, pg in frontier_pairs
                if not _dominates((child_state.battery, child_g), (b, pg))
            ]
            frontier_pairs.append((child_state.battery, child_g))
            best_g[child_state] = child_g
            child = Node(child_state, child_g, node, action)
            heapq.heappush(frontier, (child_g, next(seq), child))

    return {
        "solution_found": False,
        "total_cost": 0,
        "steps": [],
        "message": f"UCS exhausted the reachable state space; expanded {expanded} nodes.",
    }
