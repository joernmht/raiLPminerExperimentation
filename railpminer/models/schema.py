"""Structured-output schema.

Classification is part of the model itself: every element (objective,
variable, parameter, constraint) carries a ``domain_class`` from
:mod:`railpminer.analysis.taxonomy`.  **Parameters are first-class** now --
the input data/coefficients (planned times, headway, capacity, ...) are
modelled and classified explicitly, not folded into variables.
"""

from dataclasses import dataclass, field
from typing import List

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic models -- structured LLM output
# ---------------------------------------------------------------------------

class PydanticParameter(BaseModel):
    Number: int
    Symbol: str
    Name: str
    Description: str
    domain_class: str           # PARAMETER_CLASSES


class PydanticVariable(BaseModel):
    Number: int
    Symbol: str
    Name: str
    Description: str
    Domain: str                 # e.g. "continuous >= 0", "binary"
    domain_class: str           # VARIABLE_CLASSES


class PydanticEquation(BaseModel):
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]
    ParametersIncluded: List[int] = []
    domain_class: str           # OBJECTIVE_CLASSES / CONSTRAINT_CLASSES


class PydanticObjectiveFunction(PydanticEquation):
    sense: str = "min"          # "min" or "max"


class PydanticConstraint(PydanticEquation):
    pass


class Model(BaseModel):
    """A fully classified MILP."""
    parameters: List[PydanticParameter] = []
    variablesInModel: List[PydanticVariable]
    objective_function: PydanticObjectiveFunction
    constraints: List[PydanticConstraint]


# ---------------------------------------------------------------------------
# Dataclasses -- internal parser representation
# ---------------------------------------------------------------------------

@dataclass
class Parameter:
    Number: int
    Symbol: str
    Name: str
    Description: str
    domain_class: str


@dataclass
class Variable:
    Number: int
    Symbol: str
    Name: str
    Description: str
    Domain: str
    domain_class: str


@dataclass
class ObjectiveFunction:
    Name: str
    Number: int
    equation: str
    description: str
    domain_class: str
    sense: str = "min"
    VariablesIncluded: List[int] = field(default_factory=list)
    ParametersIncluded: List[int] = field(default_factory=list)


@dataclass
class Constraint:
    Name: str
    Number: int
    equation: str
    description: str
    domain_class: str
    VariablesIncluded: List[int] = field(default_factory=list)
    ParametersIncluded: List[int] = field(default_factory=list)
