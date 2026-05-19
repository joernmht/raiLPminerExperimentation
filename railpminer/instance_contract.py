"""Shared text contracts (no heavy deps, importable anywhere).

Keeping these as plain strings in a dependency-free module avoids import
cycles between the agent builders and the validation package.
"""

# The benchmark-instance dict passed to generated ``build_model`` code.
INSTANCE_CONTRACT = """\
`inst` is a dict with keys:
  trains (list), stations (ordered list),
  route (dict train -> list of station indices, in stop order),
  planned_departure (dict train -> minute),
  min_run (dict (i, j) -> minutes for consecutive station indices),
  min_dwell (dict station -> minutes), headway (number),
  station_capacity (dict station -> int), weight (dict train -> float),
  horizon (number).
"""

# What runnable code must define / return.
BUILD_MODEL_CONTRACT = """\
Define exactly:

    def build_model(inst):
        ...
        return prob, sched

where `prob` is a pulp.LpProblem with the objective and all constraints
added, and `sched` maps (train, station) -> {"arr": v, "dep": v} for every
(train, station) on that train's route (v are the pulp time variables), so
the schedule can be read back uniformly. `pulp` is already imported.
Keep every expression strictly linear (no var*var, no abs(), no min/max).
"""

# The embedded classification dict the DC baseline must also emit, so its
# code can be reverse-graphed deterministically (no a-posteriori keywords).
CLASSIFICATION_CONTRACT = """\
Also emit, at module level, a literal dict named CLASSIFICATION:

CLASSIFICATION = {
  "objective": {"sense": "min|max", "class": "<objective class>",
                "expr": "<linear objective expression>",
                "vars": ["<var symbol>", ...], "params": ["<param symbol>", ...]},
  "parameters": [{"symbol": "<sym>", "class": "<parameter class>",
                  "description": "<text>"}, ...],
  "variables":  [{"symbol": "<sym>", "class": "<variable class>",
                  "domain": "continuous|binary|integer",
                  "description": "<text>"}, ...],
  "constraints":[{"name": "<short name>", "class": "<constraint class>",
                  "expr": "<linear (in)equality>",
                  "vars": ["<sym>", ...], "params": ["<sym>", ...]}, ...],
}
Use only the class vocabularies given. The symbols in vars/params must be
exactly the variable/parameter symbols you used.
"""
