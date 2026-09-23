"""Pydantic-схемы модуля HR."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


PreviewMoney = Annotated[str, Field(
    strict=True,
    max_length=12,
    pattern=r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$",
    description="BYN, decimal string, 0..999999999.99; at most 2 fractional digits.",
)]
PreviewRate = Annotated[str, Field(
    strict=True,
    max_length=6,
    pattern=r"^(0|[1-9][0-9]{0,2})(\.[0-9]{1,2})?$",
    description="Explicit percent, 0..100; at most 2 fractional digits.",
)]


class PayrollPreviewIn(BaseModel):
    """Explicit scenario assumptions, never an instruction to accrue or pay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_net_byn: PreviewMoney
    advance_byn: PreviewMoney
    tax_deduction_byn: PreviewMoney
    other_withholding_byn: PreviewMoney
    income_tax_rate_pct: PreviewRate
    employee_fszn_rate_pct: PreviewRate
    employer_fszn_rate_pct: PreviewRate
    belgos_rate_pct: PreviewRate | None = None

    @model_validator(mode="after")
    def validate_scenario(self) -> Self:
        rates = (
            self.income_tax_rate_pct, self.employee_fszn_rate_pct,
            self.employer_fszn_rate_pct, self.belgos_rate_pct,
        )
        if any(Decimal(rate) > 100 for rate in rates if rate is not None):
            raise ValueError("Ставки должны быть в пределах от 0 до 100 процентов")
        with localcontext() as ctx:
            ctx.prec = 32
            if Decimal(self.income_tax_rate_pct) + Decimal(self.employee_fszn_rate_pct) >= 100:
                raise ValueError("Сумма ставок подоходного налога и ФСЗН работника должна быть < 100%")
        if Decimal(self.advance_byn) > Decimal(self.target_net_byn):
            raise ValueError("Аванс не может превышать месячную сумму на руки")
        return self


class PayrollPreviewOut(BaseModel):
    status: Literal["draft"] = "draft"
    currency: Literal["BYN"] = "BYN"
    inputs: PayrollPreviewIn
    gross_byn: str
    taxable_base_byn: str
    income_tax_byn: str
    employee_fszn_byn: str
    employer_fszn_byn: str
    belgos_byn: str | None
    net_byn: str
    payout_byn: str
    warnings: list[str]
    assumptions: list[str]


class PayrollPolicyMonthIn(BaseModel):
    """Documented inputs for one employee/month; None means unknown, never zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    period: Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    monthly_norm_hours: PreviewMoney | None
    worked_hours: PreviewMoney | None
    worked_days: Annotated[int, Field(ge=0, le=31)] | None
    source_ref: Annotated[str, Field(max_length=500)] = ""
    norm_hours: PreviewMoney | None = None
    shipment_revenue_byn: PreviewMoney | None = None
    shipment_cost_byn: PreviewMoney | None = None
    return_revenue_byn: PreviewMoney | None = None
    return_cost_byn: PreviewMoney | None = None
    confirmed_penalties_byn: PreviewMoney | None = None
    opening_carry_byn: PreviewMoney | None = None
    advance_byn: PreviewMoney
    tax_deduction_byn: PreviewMoney
    other_withholding_byn: PreviewMoney
    income_tax_rate_pct: PreviewRate = "13"
    employee_fszn_rate_pct: PreviewRate = "1"
    employer_fszn_rate_pct: PreviewRate = "34"
    belgos_rate_pct: PreviewRate | None = "0.52"
    mode: Literal["policy", "manual"] = "policy"
    target_net_byn: PreviewMoney | None = None
    manual_reason: str = ""
    manual_document_ref: str = ""


class PayrollPolicyEmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    employee_id: Annotated[int, Field(gt=0)]
    employee_name: Annotated[str, Field(min_length=1, max_length=255)]
    role: Literal["seller", "assemblerSenior", "assembler", "salary"]
    contractual_salary_byn: PreviewMoney | None = None
    months: Annotated[list[PayrollPolicyMonthIn], Field(min_length=1, max_length=12)]


class PayrollPolicyPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    employees: Annotated[list[PayrollPolicyEmployeeIn], Field(min_length=1, max_length=100)]


class PayrollPolicyRowOut(BaseModel):
    employee_id: int
    employee_name: str
    role: str
    period: str
    status: Literal["ready", "unknown"]
    error: str | None = None
    source_ref: str
    worked_days: int | None
    monthly_norm_hours: str | None
    worked_hours: str | None
    norm_hours: str | None
    base_byn: str | None = None
    salary_byn: str | None = None
    bonus_byn: str | None = None
    gross_byn: str | None = None
    taxable_base_byn: str | None = None
    tax_deduction_byn: str | None = None
    income_tax_byn: str | None = None
    employee_fszn_byn: str | None = None
    other_withholding_byn: str | None = None
    employer_fszn_byn: str | None = None
    belgos_byn: str | None = None
    net_byn: str | None = None
    advance_byn: str | None = None
    payout_byn: str | None = None
    manual: bool = False
    manual_reason: str | None = None
    manual_document_ref: str | None = None
    policy_net_byn: str | None = None
    seller_gross_profit_byn: str | None = None
    seller_opening_carry_byn: str | None = None
    seller_closing_carry_byn: str | None = None


class PayrollPolicyPreviewOut(BaseModel):
    status: Literal["draft"] = "draft"
    currency: Literal["BYN"] = "BYN"
    can_manual_adjust: bool
    rows: list[PayrollPolicyRowOut]
    warnings: list[str]


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
