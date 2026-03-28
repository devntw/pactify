from typing import List, Literal

from pydantic import BaseModel


class ContractInput(BaseModel):
    lender: str
    borrower: str
    amount: float
    currency: Literal["INR"] = "INR"
    deadline: str


class Obligation(BaseModel):
    from_party: str
    to_party: str
    amount: float
    currency: str
    deadline: str


class Simulation(BaseModel):
    scenario: str
    result: str
    impact: List[str]


class Consistency(BaseModel):
    valid: bool
    issues: List[str]


class ReasoningOutput(BaseModel):
    summary: str
    obligation: Obligation
    simulations: List[Simulation]
    consistency: Consistency