"""Pure monthly net-to-gross preview under explicitly supplied scenario assumptions."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from modules.hr.schemas import PayrollPreviewIn, PayrollPreviewOut

CENT = Decimal("0.01")
MAX_GROSS_CENTS = 99_999_999_999  # Same 999999999.99 BYN ceiling as request money.


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _find_gross(
    target: Decimal, deduction: Decimal, other: Decimal,
    income_rate: Decimal, employee_rate: Decimal,
) -> Decimal:
    """Find the smallest exact gross cent, without assuming rounded net is monotone.

    The sum of two cent-rounding errors is at most one cent in either direction.
    Unrounded net IS strictly increasing because the employee rates sum to < 1.
    Binary-search its lower feasible bound, then check every candidate until its
    upper feasible bound. Rates have 0.01 percentage-point precision, so this
    scan covers at most 20,001 gross cents, including both bounds.
    """
    def unrounded_net(cents: int) -> Decimal:
        gross = Decimal(cents) * CENT
        taxable = max(gross - deduction, Decimal(0))
        return gross - gross * employee_rate - taxable * income_rate - other

    lower, upper = 0, MAX_GROSS_CENTS
    while lower < upper:
        mid = (lower + upper) // 2
        if unrounded_net(mid) < target - CENT:
            lower = mid + 1
        else:
            upper = mid

    cents = lower
    while cents <= MAX_GROSS_CENTS and unrounded_net(cents) <= target + CENT:
        gross = Decimal(cents) * CENT
        taxable = max(gross - deduction, Decimal(0))
        net = gross - _money(gross * employee_rate) - _money(taxable * income_rate) - other
        if net == target:
            return gross
        cents += 1
    raise ValueError("Точный расчёт невозможен в пределах начисления 999999999.99 BYN")


def calculate_payroll_preview(payload: PayrollPreviewIn) -> PayrollPreviewOut:
    """Return a draft only; the caller cannot create a payroll entry through this function."""
    # Use a private Decimal context: caller precision must not alter money or the search bounds.
    with localcontext() as ctx:
        ctx.prec = 32
        target = Decimal(payload.target_net_byn)
        deduction = Decimal(payload.tax_deduction_byn)
        other = Decimal(payload.other_withholding_byn)
        income_rate = Decimal(payload.income_tax_rate_pct) / 100
        employee_rate = Decimal(payload.employee_fszn_rate_pct) / 100
        gross = _find_gross(target, deduction, other, income_rate, employee_rate)
        taxable = max(gross - deduction, Decimal(0))
        income_tax = _money(taxable * income_rate)
        employee_fszn = _money(gross * employee_rate)
        employer_fszn = _money(gross * Decimal(payload.employer_fszn_rate_pct) / 100)
        belgos = (
            _money(gross * Decimal(payload.belgos_rate_pct) / 100)
            if payload.belgos_rate_pct is not None else None
        )
        warnings = [
            "Черновик, не официальное начисление. Годовой доход и налоговые пороги, "
            "резидентство, льготы и основания вычетов, страховые условия не проверены.",
        ]
        if belgos is None:
            warnings.append("Тариф Белгосстраха не задан; взнос неизвестен, а не равен нулю.")
        return PayrollPreviewOut(
            inputs=payload,
            gross_byn=f"{gross:.2f}",
            taxable_base_byn=f"{taxable:.2f}",
            income_tax_byn=f"{income_tax:.2f}",
            employee_fszn_byn=f"{employee_fszn:.2f}",
            employer_fszn_byn=f"{employer_fszn:.2f}",
            belgos_byn=f"{belgos:.2f}" if belgos is not None else None,
            net_byn=f"{gross - income_tax - employee_fszn - other:.2f}",
            payout_byn=f"{target - Decimal(payload.advance_byn):.2f}",
            warnings=warnings,
            assumptions=[
                "Ставки, вычет и прочие удержания явно заданы пользователем как условия сценария; "
                "их применимость к человеку и организации не подтверждена.",
                "Каждое удержание и взнос округляются отдельно до копеек по ROUND_HALF_UP. "
                "Выбирается минимальное начисление в копейках, точно дающее заданную сумму на руки.",
                "База подоходного налога = max(начислено − вычет, 0); "
                "ФСЗН и Белгосстрах считаются от начисленного без учёта специальных ограничений базы.",
                "На руки = начислено − подоходный налог − ФСЗН работника − прочие удержания. "
                "Взносы нанимателя не удерживаются у работника.",
                "На руки — сумма за месяц до аванса. Аванс уменьшает только остаток к выплате.",
            ],
        )
