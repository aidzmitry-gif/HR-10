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
