"""Pydantic-схемы модуля HR."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
    employee_id: int
    period: str  # "YYYY-MM"
    amount_byn: str  # Decimal as str


class PayrollPayIn(BaseModel):
    employee_id: int
    period: str  # "YYYY-MM"


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
