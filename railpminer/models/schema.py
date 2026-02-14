"""Data models for LLM structured output (Pydantic) and internal analysis (dataclasses)."""

from dataclasses import dataclass
from typing import List

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic models — used as output_type for LLM agents (structured output)
# ---------------------------------------------------------------------------

class PydanticVariable(BaseModel):
    Number: int
    Abbreviation: str
    Name: str
    Description: str


class PydanticEquation(BaseModel):
    """Base class for both objective functions and constraints."""
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]


class PydanticObjectiveFunction(PydanticEquation):
    pass


class PydanticConstraint(PydanticEquation):
    pass


class Model(BaseModel):
    """Complete LP model as returned by the LLM graphing agent."""
    variablesInModel: List[PydanticVariable]
    objective_function: PydanticObjectiveFunction
    constraints: List[PydanticConstraint]


# ---------------------------------------------------------------------------
# Dataclasses — used internally by the graph parser (safe_eval_model)
# ---------------------------------------------------------------------------

@dataclass
class Variable:
    Number: int
    Abbreviation: str
    Name: str
    Description: str


@dataclass
class ObjectiveFunction:
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]


@dataclass
class Constraint:
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]
