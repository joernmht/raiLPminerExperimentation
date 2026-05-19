"""Operational-safety checks on a solved schedule.

Reviewer #2/#4 stressed that a solvable model is still worthless if it
violates basic railway operating rules (running times, dwell, headway,
station capacity).  These checks read the schedule a generated model
produced on an instance and verify those rules directly, with a small
numerical tolerance.  They only apply when the model solved to optimality
and exposed a ``sched`` mapping; otherwise safety is reported as ``None``
(not applicable) rather than silently "passed".
"""

from typing import Dict, Optional

TOL = 1e-6


def _route_pairs(route):
    return [(route[i], route[i + 1]) for i in range(len(route) - 1)]


def check_schedule(schedule: Dict, inst: Dict) -> Dict[str, Optional[bool]]:
    """Return per-rule pass/fail plus an overall ``railway_safe`` flag.

    ``schedule`` is ``{(train, station): {"arr": float, "dep": float}}`` as
    read back from the solved model.  Returns ``railway_safe = None`` if no
    usable schedule is available.
    """
    if not schedule:
        return {
            "departure_not_early": None, "min_run_ok": None,
            "min_dwell_ok": None, "headway_ok": None,
            "capacity_ok": None, "railway_safe": None,
        }

    def t(train, station, kind):
        cell = schedule.get((train, station))
        if not cell:
            return None
        return cell.get(kind)

    dep_not_early = True
    run_ok = True
    dwell_ok = True

    for tr in inst["trains"]:
        route = inst["route"][tr]
        if not route:
            continue
        origin = route[0]
        d0 = t(tr, origin, "dep")
        if d0 is not None and d0 + TOL < inst["planned_departure"][tr]:
            dep_not_early = False

        for a, b in _route_pairs(route):
            dep_a = t(tr, a, "dep")
            arr_b = t(tr, b, "arr")
            need = inst["min_run"].get((a, b))
            if dep_a is not None and arr_b is not None and need is not None:
                if arr_b - dep_a + TOL < need:
                    run_ok = False

        for st in route[1:-1] if len(route) > 2 else []:
            arr = t(tr, st, "arr")
            dep = t(tr, st, "dep")
            need = inst["min_dwell"].get(st, 0)
            if arr is not None and dep is not None and dep - arr + TOL < need:
                dwell_ok = False

    # Headway: at every station, ordered departure times of trains passing
    # it must be separated by at least the headway.
    headway_ok = True
    hw = inst["headway"]
    for st in inst["stations"]:
        times = sorted(
            t(tr, st, "dep")
            for tr in inst["trains"]
            if st in inst["route"][tr] and t(tr, st, "dep") is not None
        )
        for x, y in zip(times, times[1:]):
            if y - x + TOL < hw:
                headway_ok = False

    # Station capacity: max simultaneous occupancy [arr, dep] per station.
    capacity_ok = True
    for st in inst["stations"]:
        events = []
        for tr in inst["trains"]:
            if st not in inst["route"][tr]:
                continue
            arr = t(tr, st, "arr")
            dep = t(tr, st, "dep")
            if arr is None or dep is None:
                continue
            events.append((arr, 1))
            events.append((dep, -1))
        events.sort()
        occ = 0
        cap = inst["station_capacity"].get(st, len(inst["trains"]))
        for _, delta in events:
            occ += delta
            if occ > cap:
                capacity_ok = False

    rules = {
        "departure_not_early": dep_not_early,
        "min_run_ok": run_ok,
        "min_dwell_ok": dwell_ok,
        "headway_ok": headway_ok,
        "capacity_ok": capacity_ok,
    }
    rules["railway_safe"] = all(rules.values())
    return rules
