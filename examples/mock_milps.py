"""Mock MILPs for the offline dry run.

Four representative cases, each as (a) the strict-format MILP text the graph
screen consumes and (b) a pre-written ``build_model`` source so the solver
validation runs with no LLM / network:

* ``good``       -- a correct, linear, railway-safe rescheduling MILP.
* ``nonlinear``  -- contains a product of variables (Reviewer #4.5): the
                    graph screen flags it AND PuLP rejects it.
* ``incomplete`` -- a variable left unwired + contradictory constraints:
                    screen says incomplete, solver says infeasible.
* ``empty``      -- correct section headers but no mathematical content
                    (the case Reviewer #2 highlighted): the old
                    completeness check "passed" it; the deterministic parser
                    and the solver both reject it.
"""

# --------------------------------------------------------------------------
# 1. GOOD -- complete, coherent, linear, solves, railway-safe
# --------------------------------------------------------------------------
GOOD_TEXT = """\
VARIABLES
v1: arr -- continuous >= 0 -- arrival time of a train at a station
v2: dep -- continuous >= 0 -- departure time of a train at a station
v3: cancel -- binary -- 1 if a train service is cancelled
OBJECTIVE
min: w * arr - w * planned + bigM * cancel -- weighted final delay plus cancellation penalty
CONSTRAINTS
c1: dep >= planned - bigM * cancel -- a train may not depart before its planned departure time (unless cancelled)
c2: arr >= dep + min_run -- running time on a segment is at least the minimum running time
c3: dep >= arr + min_dwell -- dwell time at a stop is at least the minimum dwell time (headway respected via ordering)
c4: arr <= horizon + bigM * cancel -- a cancelled train imposes no schedule
c5: dep <= horizon + bigM * cancel -- a cancelled train imposes no schedule
"""

GOOD_CODE = '''
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

    prob += (
        pulp.lpSum(w[t] * (arr[t, route[t][-1]] - pdep[t]) for t in trains)
        + 10000 * pulp.lpSum(cancel.values())
    )
    return prob, sched
'''

# --------------------------------------------------------------------------
# 2. NONLINEAR -- product of two decision variables
# --------------------------------------------------------------------------
NONLINEAR_TEXT = """\
VARIABLES
v1: arr -- continuous >= 0 -- arrival time
v2: dep -- continuous >= 0 -- departure time
OBJECTIVE
min: arr * dep -- (invalid) product of two decision variables
CONSTRAINTS
c1: arr >= dep + min_run -- running time
c2: dep >= planned -- not before planned departure
"""

NONLINEAR_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("nl", pulp.LpMinimize)
    sched = {}
    a = pulp.LpVariable("a", 0, 100)
    d = pulp.LpVariable("d", 0, 100)
    prob += a * d            # non-linear: PuLP raises here
    prob += a >= d + 5
    return prob, sched
'''

# --------------------------------------------------------------------------
# 3. INCOMPLETE -- unwired variable + contradictory constraints
# --------------------------------------------------------------------------
INCOMPLETE_TEXT = """\
VARIABLES
v1: dep -- continuous >= 0 -- departure time
v2: slack -- continuous >= 0 -- never used anywhere
OBJECTIVE
min: dep -- minimise departure
CONSTRAINTS
c1: dep >= planned -- not before planned departure
c2: dep <= 0 -- (contradicts c1 when planned > 0)
"""

INCOMPLETE_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("inf", pulp.LpMinimize)
    sched = {}
    d = pulp.LpVariable("d", 0, 100)
    prob += d
    prob += d >= 10          # planned > 0
    prob += d <= 0           # contradiction -> infeasible
    return prob, sched
'''

# --------------------------------------------------------------------------
# 4. EMPTY -- valid headers, zero mathematical content
# --------------------------------------------------------------------------
EMPTY_TEXT = """\
VARIABLES
OBJECTIVE
CONSTRAINTS
"""

EMPTY_CODE = '''
def build_model(inst):
    prob = pulp.LpProblem("empty", pulp.LpMinimize)
    return prob, {}
'''


MOCK_MILPS = [
    {"case": "good", "answer": GOOD_TEXT, "solver_code": GOOD_CODE},
    {"case": "nonlinear", "answer": NONLINEAR_TEXT, "solver_code": NONLINEAR_CODE},
    {"case": "incomplete", "answer": INCOMPLETE_TEXT, "solver_code": INCOMPLETE_CODE},
    {"case": "empty", "answer": EMPTY_TEXT, "solver_code": EMPTY_CODE},
]
