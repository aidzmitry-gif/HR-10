"""Pydantic-схемы модуля HR."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmployeeCreate(BaseModel):
    full_name: str
    position: str = ""
    department: str = ""
    status: str = "active"


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    position: str
    department: str
    status: str
    profile_status: str = "not_started"


class CandidateCreate(BaseModel):
    name: str = ""
    position: str = ""
    salary: float = 0
    recruiter: str = ""
    priority: str = "Средний"
    stage: str = "new"
    next_step: str = ""
    number: str = ""


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    name: str
    position: str
    salary: float
    recruiter: str
    priority: str
    stage: str
    next_step: str


class StageUpdate(BaseModel):
    stage: str


class PayrollAccrueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0, strict=True)
    period: str = Field(strict=True, pattern=r"^[1-9][0-9]{3}-(?:0[1-9]|1[0-2])$")
    amount_byn: str = Field(strict=True, pattern=r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$")

    @field_validator("amount_byn")
    @classmethod
    def positive_byn(cls, value: str) -> str:
        amount = Decimal(value)
        if amount <= 0:
            raise ValueError("Payroll amount must be positive")
        return f"{amount:.2f}"


class PayrollPayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0, strict=True)
    period: str = Field(strict=True, pattern=r"^[1-9][0-9]{3}-(?:0[1-9]|1[0-2])$")
    entry_id: int | None = Field(default=None, gt=0, strict=True)


class PayrollEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    period: str
    amount_byn: str
    status: str


class PayrollPeriodSummary(BaseModel):
    period: str        # "YYYY-MM"
    total_byn: str     # сумма amount_byn через Decimal, возвращать str
    count: int
    pending_count: int


class OkkScoreCreate(BaseModel):
    employee_id: int
    period: str  # "YYYY-MM"
    discipline: int = 0  # 0-25
    quality: int = 0  # 0-25
    service: int = 0  # 0-25
    teamwork: int = 0  # 0-25
    comment: str = ""


class OkkScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    period: str
    discipline: int
    quality: int
    service: int
    teamwork: int
    total: int
    comment: str
