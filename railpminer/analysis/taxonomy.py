"""Comprehensive railway-rescheduling MILP taxonomy.

Classification is now part of *generation and parsing*, not an a-posteriori
keyword pass.  Every model element carries a domain class drawn from the
vocabularies below, spanning all four element kinds:

* the **objective function**            (:data:`OBJECTIVE_CLASSES`)
* the decision **variables**            (:data:`VARIABLE_CLASSES`)
* the input **parameters** (data/coeffs) (:data:`PARAMETER_CLASSES`)
* the **constraints**                    (:data:`CONSTRAINT_CLASSES`)

The taxonomy is embedded in the generation prompt (the LLM must tag each
element) and read back deterministically by the parser, so domain relevance
is a *declared, parsed* property -- no synonym/keyword undercounting.

Unknown / free-text labels are normalised to the matching ``other_*`` bucket
by :func:`normalize` so a mistagged element is still placed, never dropped.
"""

OBJECTIVE_CLASSES = {
    "min_total_delay",
    "min_weighted_delay",
    "min_max_delay",
    "min_total_cancellations",
    "min_timetable_deviation",
    "min_energy_or_cost",
    "multi_objective",
    "other_objective",
}

VARIABLE_CLASSES = {
    "event_time",        # arrival / departure / occupation time (continuous)
    "delay",             # delay magnitude (continuous)
    "ordering",          # binary precedence / sequencing
    "routing",           # binary route / track selection
    "cancellation",      # binary cancel-service
    "assignment",        # binary platform / track assignment
    "auxiliary",         # linearisation / Big-M helper
    "other_variable",
}

PARAMETER_CLASSES = {
    "planned_time",          # scheduled arrival / departure
    "min_running_time",
    "min_dwell_time",
    "headway",
    "capacity",              # station / track / block capacity
    "weight_priority",
    "disruption_window",     # blockage start/end, unavailable resource
    "big_m",
    "network_topology",      # segments, routes, stop patterns
    "other_parameter",
}

CONSTRAINT_CLASSES = {
    "running_time",
    "dwell_time",
    "headway_separation",
    "station_capacity",
    "block_occupation",
    "ordering_consistency",
    "routing_selection",
    "cancellation_logic",
    "flow_balance",
    "connection",            # passenger / rolling-stock connection
    "bound_domain",          # variable bounds / domain
    "big_m_linking",
    "objective_linking",
    "other_constraint",
}

CLASSES = {
    "objective": OBJECTIVE_CLASSES,
    "variable": VARIABLE_CLASSES,
    "parameter": PARAMETER_CLASSES,
    "constraint": CONSTRAINT_CLASSES,
}

#: The operationally essential constraint classes a *safe* railway
#: rescheduling model must contain (used by the early screen and tested
#: against the solver safety result).
SAFETY_CONSTRAINT_CLASSES = {
    "running_time",
    "headway_separation",
    "station_capacity",
}


def normalize(kind: str, label) -> str:
    """Map a raw label to a valid class for ``kind``.

    Case-insensitive, tolerant of spaces/hyphens.  Anything unrecognised
    becomes the ``other_*`` bucket for that kind so it is still counted.
    """
    valid = CLASSES[kind]
    if label is None:
        return f"other_{kind}"
    key = str(label).strip().lower().replace(" ", "_").replace("-", "_")
    if key in valid:
        return key
    return f"other_{kind}"


def prompt_block() -> str:
    """Human-readable taxonomy block injected into generation prompts."""
    lines = []
    for kind, vocab in (
        ("OBJECTIVE class", OBJECTIVE_CLASSES),
        ("VARIABLE class", VARIABLE_CLASSES),
        ("PARAMETER class", PARAMETER_CLASSES),
        ("CONSTRAINT class", CONSTRAINT_CLASSES),
    ):
        lines.append(f"{kind}: " + ", ".join(sorted(vocab)))
    return "\n".join(lines)
