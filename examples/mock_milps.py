"""Mock data for the offline dry run -- new classified format, 3 workflows.

Each row has a ``workflow`` (SIM / TAF / DC), a ``case`` label, an
``answer`` (classified MILP text for SIM/TAF, runnable code + embedded
CLASSIFICATION dict for DC) and a precomputed ``solver_code`` so the whole
pipeline runs offline.  The set is built so the cheap graph screen and the
solver ground truth can be compared per workflow.
"""

# ===========================================================================
# Shared working solver code (solves + railway-safe on all benchmarks)
# ===========================================================================
SAFE_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("resched", pulp.LpMinimize)
    trains, stations = inst["trains"], inst["stations"]
    route, pdep = inst["route"], inst["planned_departure"]
    minrun, dwell = inst["min_run"], inst["min_dwell"]
    hw, w = inst["headway"], inst["weight"]
    cap, H = inst["station_capacity"], inst["horizon"]
    arr, dep, sched = {}, {}, {}
    for t in trains:
        for s in route[t]:
            arr[t, s] = pulp.LpVariable(f"arr_{t}_{s}", 0, H)
            dep[t, s] = pulp.LpVariable(f"dep_{t}_{s}", 0, H)
            sched[(t, s)] = {"arr": arr[t, s], "dep": dep[t, s]}
    cancel = {t: pulp.LpVariable(f"cancel_{t}", cat="Binary") for t in trains}
    for t in trains:
        r = route[t]
        prob += dep[t, r[0]] >= pdep[t]
        prob += arr[t, r[0]] == dep[t, r[0]]
        for k in range(len(r) - 1):
            a, b = r[k], r[k + 1]
            prob += arr[t, b] >= dep[t, a] + minrun[(a, b)]
        for s in r[1:]:
            prob += dep[t, s] >= arr[t, s] + dwell[s]
    order = sorted(trains, key=lambda t: pdep[t])
    for s in stations:
        passing = [t for t in order if s in route[t]]
        for i in range(len(passing) - 1):
            t1, t2 = passing[i], passing[i + 1]
            prob += dep[t2, s] >= dep[t1, s] + hw
            prob += arr[t2, s] >= arr[t1, s] + hw
        c = cap.get(s, len(passing))
        for i in range(len(passing)):
            j = i + c
            if j < len(passing):
                prob += arr[passing[j], s] >= dep[passing[i], s]
    prob += (pulp.lpSum(w[t] * (arr[t, route[t][-1]] - pdep[t]) for t in trains)
             + 10000 * pulp.lpSum(cancel.values()))
    return prob, sched
'''

# ===========================================================================
# SIM / TAF: classified MILP text (new ' | ' delimited format)
# ===========================================================================
GOOD_TEXT = """\
PARAMETERS
p1: planned | planned_time | scheduled departure time
p2: min_run | min_running_time | minimum running time on a segment
p3: min_dwell | min_dwell_time | minimum dwell at a stop
p4: headway | headway | minimum separation between trains
p5: cap | capacity | station capacity
p6: w | weight_priority | train delay weight
p7: bigM | big_m | large constant
VARIABLES
v1: arr | event_time | continuous >= 0 | arrival time of a train at a station
v2: dep | event_time | continuous >= 0 | departure time of a train at a station
v3: cancel | cancellation | binary | 1 if a train service is cancelled
OBJECTIVE
o: min | min_weighted_delay | w * arr - w * planned + bigM * cancel | weighted final delay plus cancellation penalty
CONSTRAINTS
c1: bound_domain | dep >= planned - bigM * cancel | not before planned departure unless cancelled
c2: running_time | arr >= dep + min_run | running time at least the minimum
c3: dwell_time | dep >= arr + min_dwell | dwell at least the minimum
c4: headway_separation | dep >= planned + headway | consecutive-train headway
c5: station_capacity | arr <= cap * bigM | station capacity bound
"""

NONLINEAR_TEXT = """\
PARAMETERS
p1: min_run | min_running_time | minimum running time
VARIABLES
v1: arr | event_time | continuous >= 0 | arrival time
v2: dep | event_time | continuous >= 0 | departure time
OBJECTIVE
o: min | min_total_delay | arr * dep | (invalid) product of two variables
CONSTRAINTS
c1: running_time | arr >= dep + min_run | running time
"""

INCOMPLETE_TEXT = """\
PARAMETERS
p1: planned | planned_time | scheduled departure
VARIABLES
v1: dep | event_time | continuous >= 0 | departure time
v2: slack | auxiliary | continuous >= 0 | never used anywhere
OBJECTIVE
o: min | min_total_delay | dep | minimise departure
CONSTRAINTS
c1: bound_domain | dep >= planned | not before planned departure
c2: bound_domain | dep <= 0 | contradicts c1 when planned > 0
"""

EMPTY_TEXT = "PARAMETERS\nVARIABLES\nOBJECTIVE\nCONSTRAINTS\n"

INCOMPLETE_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("inf", pulp.LpMinimize)
    d = pulp.LpVariable("d", 0, 100)
    prob += d
    prob += d >= 10
    prob += d <= 0
    return prob, {}
'''

NONLINEAR_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("nl", pulp.LpMinimize)
    a = pulp.LpVariable("a", 0, 100)
    d = pulp.LpVariable("d", 0, 100)
    prob += a * d
    prob += a >= d + 5
    return prob, {}
'''

EMPTY_CODE = '''
def build_model(inst):
    return pulp.LpProblem("empty", pulp.LpMinimize), {}
'''

# ===========================================================================
# DC baseline: code + embedded CLASSIFICATION dict (reverse-graphed)
# ===========================================================================
_DC_CLASS_SAFE = '''
CLASSIFICATION = {
  "objective": {"sense": "min", "class": "min_weighted_delay",
                "expr": "w*(arr-planned)+10000*cancel",
                "vars": ["arr", "cancel"], "params": ["w", "planned"]},
  "parameters": [
    {"symbol": "planned", "class": "planned_time", "description": "scheduled departure"},
    {"symbol": "min_run", "class": "min_running_time", "description": "min running time"},
    {"symbol": "min_dwell", "class": "min_dwell_time", "description": "min dwell"},
    {"symbol": "headway", "class": "headway", "description": "min separation"},
    {"symbol": "cap", "class": "capacity", "description": "station capacity"},
    {"symbol": "w", "class": "weight_priority", "description": "delay weight"}],
  "variables": [
    {"symbol": "arr", "class": "event_time", "domain": "continuous", "description": "arrival"},
    {"symbol": "dep", "class": "event_time", "domain": "continuous", "description": "departure"},
    {"symbol": "cancel", "class": "cancellation", "domain": "binary", "description": "cancel"}],
  "constraints": [
    {"name": "running", "class": "running_time", "expr": "arr>=dep+min_run",
     "vars": ["arr", "dep"], "params": ["min_run"]},
    {"name": "dwell", "class": "dwell_time", "expr": "dep>=arr+min_dwell",
     "vars": ["dep", "arr"], "params": ["min_dwell"]},
    {"name": "headway", "class": "headway_separation", "expr": "dep2>=dep+headway",
     "vars": ["dep"], "params": ["headway"]},
    {"name": "capacity", "class": "station_capacity", "expr": "arr2>=dep",
     "vars": ["arr", "dep"], "params": ["cap"]},
    {"name": "cancel_link", "class": "cancellation_logic", "expr": "dep>=planned-M*cancel",
     "vars": ["dep", "cancel"], "params": ["planned"]}],
}
'''

# DC variant that omits headway & capacity: solves but is unsafe, and its
# CLASSIFICATION lacks the safety triad -> the screen flags it too.
_DC_CLASS_UNSAFE = '''
CLASSIFICATION = {
  "objective": {"sense": "min", "class": "min_total_delay",
                "expr": "arr", "vars": ["arr"], "params": ["planned"]},
  "parameters": [
    {"symbol": "planned", "class": "planned_time", "description": "scheduled departure"},
    {"symbol": "min_run", "class": "min_running_time", "description": "min running time"}],
  "variables": [
    {"symbol": "arr", "class": "event_time", "domain": "continuous", "description": "arrival"},
    {"symbol": "dep", "class": "event_time", "domain": "continuous", "description": "departure"}],
  "constraints": [
    {"name": "running", "class": "running_time", "expr": "arr>=dep+min_run",
     "vars": ["arr", "dep"], "params": ["min_run"]},
    {"name": "planned", "class": "bound_domain", "expr": "dep>=planned",
     "vars": ["dep"], "params": ["planned"]}],
}
'''

DC_UNSAFE_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("dc_unsafe", pulp.LpMinimize)
    trains, route = inst["trains"], inst["route"]
    pdep, minrun = inst["planned_departure"], inst["min_run"]
    dwell, H = inst["min_dwell"], inst["horizon"]
    arr, dep, sched = {}, {}, {}
    for t in trains:
        for s in route[t]:
            arr[t, s] = pulp.LpVariable(f"arr_{t}_{s}", 0, H)
            dep[t, s] = pulp.LpVariable(f"dep_{t}_{s}", 0, H)
            sched[(t, s)] = {"arr": arr[t, s], "dep": dep[t, s]}
    for t in trains:
        r = route[t]
        prob += dep[t, r[0]] >= pdep[t]
        prob += arr[t, r[0]] == dep[t, r[0]]
        for k in range(len(r) - 1):
            a, b = r[k], r[k + 1]
            prob += arr[t, b] >= dep[t, a] + minrun[(a, b)]
        for s in r[1:]:
            prob += dep[t, s] >= arr[t, s] + dwell[s]
    prob += pulp.lpSum(arr[t, route[t][-1]] for t in trains)
    return prob, sched
'''

DC_GOOD = SAFE_CODE + _DC_CLASS_SAFE
DC_UNSAFE = DC_UNSAFE_CODE + _DC_CLASS_UNSAFE


MOCK_MILPS = [
    {"workflow": "SIM", "case": "good", "answer": GOOD_TEXT, "solver_code": SAFE_CODE},
    {"workflow": "TAF", "case": "good", "answer": GOOD_TEXT, "solver_code": SAFE_CODE},
    {"workflow": "DC", "case": "good", "answer": DC_GOOD, "solver_code": DC_GOOD},
    {"workflow": "SIM", "case": "nonlinear", "answer": NONLINEAR_TEXT, "solver_code": NONLINEAR_CODE},
    {"workflow": "SIM", "case": "incomplete", "answer": INCOMPLETE_TEXT, "solver_code": INCOMPLETE_CODE},
    {"workflow": "SIM", "case": "empty", "answer": EMPTY_TEXT, "solver_code": EMPTY_CODE},
    {"workflow": "DC", "case": "missing_safety", "answer": DC_UNSAFE, "solver_code": DC_UNSAFE},
]
