"""Benchmark railway-rescheduling instances for solver validation.

Every generated MILP is solved on *several* concrete instances so that
feasibility / optimality / runtime and operational safety are measured, not
assumed.  The instances are small, fully specified and deterministic so the
whole validation is reproducible without external data.

Instance schema (a plain dict, passed to generated ``build_model``)
-------------------------------------------------------------------
====================  =====================================================
``trains``            list of train ids
``stations``          ordered list of station ids (index = position on line)
``route``             ``{train: [station_idx, ...]}`` -- stops in order
``planned_departure`` ``{train: minute}`` planned departure from its origin
``min_run``           ``{(station_i, station_j): minutes}`` min running time
``min_dwell``         ``{station: minutes}`` minimum dwell at a stop
``headway``           scalar minimum separation (minutes) between two trains
``station_capacity``  ``{station: int}`` simultaneous trains allowed
``weight``            ``{train: float}`` delay weight (priority)
``horizon``           scheduling horizon upper bound (minutes)
====================  =====================================================

A generated solver model is asked (see :mod:`validation.codegen`) to expose
its schedule through ``sched[(train, station)] = {"arr": var, "dep": var}``
so the operational-safety checks can read any model uniformly.
"""

from copy import deepcopy
from typing import Dict, List


def _instance(name, trains, stations, route, planned, min_run,
              dwell, headway, cap, weight, horizon):
    return {
        "name": name,
        "trains": trains,
        "stations": stations,
        "route": route,
        "planned_departure": planned,
        "min_run": min_run,
        "min_dwell": {s: dwell for s in stations},
        "headway": headway,
        "station_capacity": {s: cap for s in stations},
        "weight": weight,
        "horizon": horizon,
    }


def _line(n_stations, run_minutes):
    stations = list(range(n_stations))
    min_run = {
        (i, i + 1): run_minutes for i in range(n_stations - 1)
    }
    return stations, min_run


def small_corridor() -> Dict:
    """3 trains, 4 stations, same direction -- comfortably feasible."""
    stations, min_run = _line(4, 8)
    trains = ["T1", "T2", "T3"]
    route = {t: stations[:] for t in trains}
    planned = {"T1": 0, "T2": 5, "T3": 10}
    return _instance(
        "small_corridor", trains, stations, route, planned, min_run,
        dwell=2, headway=3, cap=2, weight={t: 1.0 for t in trains},
        horizon=120,
    )


def tight_headway() -> Dict:
    """4 trains departing close together -- headway is binding."""
    stations, min_run = _line(4, 7)
    trains = ["T1", "T2", "T3", "T4"]
    route = {t: stations[:] for t in trains}
    planned = {"T1": 0, "T2": 2, "T3": 4, "T4": 6}
    return _instance(
        "tight_headway", trains, stations, route, planned, min_run,
        dwell=1, headway=5, cap=2, weight={"T1": 2.0, "T2": 1.0,
                                           "T3": 1.0, "T4": 1.0},
        horizon=150,
    )


def capacity_pressure() -> Dict:
    """5 trains, small stations -- station capacity is binding."""
    stations, min_run = _line(5, 6)
    trains = [f"T{i}" for i in range(1, 6)]
    route = {t: stations[:] for t in trains}
    planned = {t: 3 * i for i, t in enumerate(trains)}
    return _instance(
        "capacity_pressure", trains, stations, route, planned, min_run,
        dwell=3, headway=2, cap=1, weight={t: 1.0 for t in trains},
        horizon=200,
    )


def all_instances() -> List[Dict]:
    """Return deep copies of every benchmark instance."""
    return [deepcopy(i) for i in (
        small_corridor(), tight_headway(), capacity_pressure()
    )]
