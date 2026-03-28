from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class ProcessRequest(BaseModel):
    phone: str
    text: str
    audio_path: str = ""
    message_sid: str = ""


class Party(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None


class AmountTerms(BaseModel):
    value: Optional[float] = None
    currency: Literal["INR"] = "INR"


class Terms(BaseModel):
    amount: AmountTerms = Field(default_factory=AmountTerms)
    timeline: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)


class Obligation(BaseModel):
    party: Optional[str] = None
    duty: str


class ContractPayload(BaseModel):
    type: Literal[
        "loan", "sale", "service", "rent", "partnership", "unknown"
    ] = "unknown"
    parties: List[Party] = Field(default_factory=list)
    subject: Optional[str] = None
    consideration: Optional[str] = None
    terms: Terms = Field(default_factory=Terms)
    obligations: List[Obligation] = Field(default_factory=list)


class ProcessResponse(BaseModel):
    contract: ContractPayload
    missing_fields: List[str]
    is_complete: bool
    next_question: str
    confirmation_required: bool = False
    confirmation_status: Literal["not_started", "pending", "confirmed"] = "not_started"
    consent_code: Optional[str] = None
    counterparty_phone: Optional[str] = None


class ConfirmRequest(BaseModel):
    phone: str
    code: str
    message_sid: str = ""


class ConfirmResponse(BaseModel):
    status: Literal["confirmed", "rejected", "not_found", "already_confirmed"]
    message: str
    contract: ContractPayload = Field(default_factory=ContractPayload)
    initiator_phone: str = ""
    confirmer_phone: str = ""


def empty_contract_dict() -> dict:
    return ContractPayload().model_dump()
