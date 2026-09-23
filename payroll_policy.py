"""Document-sourced, read-only monthly payroll policy draft.

This intentionally does not infer a payroll fact from CRM names, production's
days/22 approximation, or best-effort sales margin events.
"""
from __future__ import annotations

import calendar
from decimal import Decimal, localcontext

from modules.hr.payroll_preview import _find_gross, _money
from modules.hr.schemas import (
    PayrollPolicyEmployeeIn, PayrollPolicyMonthIn, PayrollPolicyPreviewIn,
    PayrollPolicyPreviewOut, PayrollPolicyRowOut,
)

ZERO = Decimal("0")
ROLE_BASE = {
    "seller": Decimal("1500"),
    "assemblerSenior": Decimal("1200"),
    "assembler": Decimal("1000"),
}
ROLE_NORM_RATE = {"assemblerSenior": Decimal("7"), "assembler": Decimal("6.8")}
MAX_HOURS = Decimal("744")


def _fmt(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _base_row(employee: PayrollPolicyEmployeeIn, month: PayrollPolicyMonthIn) -> dict:
    return dict(
        employee_id=employee.employee_id, employee_name=employee.employee_name,
        role=employee.role, period=month.period, status="unknown", error=None,
        source_ref=month.source_ref.strip(), worked_days=month.worked_days,
        monthly_norm_hours=month.monthly_norm_hours,
        worked_hours=month.worked_hours, norm_hours=month.norm_hours,
    )


def _required(value: str | None, label: str) -> Decimal:
    if value is None:
        raise ValueError(f"Нет подтверждённого значения: {label}; 0 введите явно")
    return Decimal(value)


def _rates(month: PayrollPolicyMonthIn) -> list[Decimal]:
    rates = [
        Decimal(month.income_tax_rate_pct), Decimal(month.employee_fszn_rate_pct),
        Decimal(month.employer_fszn_rate_pct),
    ]
    if month.belgos_rate_pct is None:
        raise ValueError("Тариф Белгосстраха неизвестен")
    rates.append(Decimal(month.belgos_rate_pct))
    if any(rate > 100 for rate in rates) or rates[0] + rates[1] >= 100:
        raise ValueError("Недопустимые ставки удержаний или взносов")
    return rates


def _breakdown(gross: Decimal, month: PayrollPolicyMonthIn, *, check_advance: bool = True) -> dict:
    rates = _rates(month)
    deduction = Decimal(month.tax_deduction_byn)
    other = Decimal(month.other_withholding_byn)
    taxable = max(gross - deduction, ZERO)
    tax = _money(taxable * rates[0] / 100)
    worker = _money(gross * rates[1] / 100)
    net = gross - tax - worker - other
    advance = Decimal(month.advance_byn)
    if check_advance and (net < ZERO or advance > net):
        raise ValueError("Удержания или аванс превышают месячную сумму на руки")
    return dict(
        gross_byn=_fmt(gross), taxable_base_byn=_fmt(taxable),
        income_tax_byn=_fmt(tax), employee_fszn_byn=_fmt(worker),
        other_withholding_byn=_fmt(other),
        employer_fszn_byn=_fmt(_money(gross * rates[2] / 100)),
        belgos_byn=_fmt(_money(gross * rates[3] / 100)),
        net_byn=_fmt(net), advance_byn=_fmt(advance),
        payout_byn=_fmt(net - advance),
    )


def _month(
    employee: PayrollPolicyEmployeeIn, month: PayrollPolicyMonthIn,
    carry: Decimal | None, first: bool,
) -> tuple[PayrollPolicyRowOut, Decimal | None]:
    row = _base_row(employee, month)
    try:
        if len(month.source_ref.strip()) < 5:
            raise ValueError("Укажите документ-основание табеля и расчётных показателей")
        norm = _required(month.monthly_norm_hours, "месячная норма часов")
        worked = _required(month.worked_hours, "отработанные часы")
        if month.worked_days is None:
            raise ValueError("Отработанные дни неизвестны")
        year, number = map(int, month.period.split("-"))
        if month.worked_days > calendar.monthrange(year, number)[1]:
            raise ValueError("Отработанные дни превышают число дней месяца")
        if (worked == ZERO) != (month.worked_days == 0):
            raise ValueError("Отработанные дни и часы противоречат друг другу")
        if not ZERO < norm <= MAX_HOURS or worked > norm:
            raise ValueError("Норма должна быть 0..744 часов, сверхурочные требуют отдельного правила")
        base = ROLE_BASE.get(employee.role)
        if base is None:
            base = _required(employee.contractual_salary_byn, "договорной оклад")
        if base > Decimal("10000000"):
            raise ValueError("Оклад превышает предел чернового расчёта")
        salary = _money(base * worked / norm)
        row["base_byn"] = _fmt(base)
        bonus = ZERO
        next_carry = carry
        policy_error: str | None = None
        if employee.role == "seller":
            try:
                if first:
                    carry = _required(month.opening_carry_byn, "входящий перенос продавца")
                elif carry is None:
                    raise ValueError("Предыдущий месяц продавца не рассчитан; перенос неизвестен")
                elif month.opening_carry_byn is not None and Decimal(month.opening_carry_byn) != carry:
                    raise ValueError("Указанный перенос не совпадает с расчётом предыдущего месяца")
                revenue = _required(month.shipment_revenue_byn, "отгрузка без НДС")
                cost = _required(month.shipment_cost_byn, "себестоимость отгрузки")
                returns = _required(month.return_revenue_byn, "возвраты без НДС")
                return_cost = _required(month.return_cost_byn, "себестоимость возвратов")
                penalties = _required(month.confirmed_penalties_byn, "подтверждённые штрафы")
                profit = revenue - cost - returns + return_cost - penalties
                adjusted = profit - carry
                bonus = _money(max(adjusted, ZERO) * Decimal("0.05"))
                next_carry = max(-adjusted, ZERO)
                row.update(seller_gross_profit_byn=_fmt(profit),
                           seller_opening_carry_byn=_fmt(carry),
                           seller_closing_carry_byn=_fmt(next_carry))
            except ValueError as exc:
                policy_error = str(exc)
                next_carry = None
        elif employee.role in ROLE_NORM_RATE:
            try:
                completed = _required(month.norm_hours, "выполненные нормо-часы")
                if completed > Decimal("100000"):
                    raise ValueError("Нормо-часы превышают предел чернового расчёта")
                bonus = _money(completed * ROLE_NORM_RATE[employee.role])
            except ValueError as exc:
                policy_error = str(exc)
        if policy_error and month.mode == "policy":
            raise ValueError(policy_error)
        policy = _breakdown(salary + bonus, month, check_advance=month.mode == "policy") if policy_error is None else None
        if policy is not None and month.mode == "manual" and Decimal(policy["net_byn"]) < ZERO:
            policy = None
        row["policy_net_byn"] = policy["net_byn"] if policy else None
        if month.mode == "manual":
            if len(month.manual_reason.strip()) < 8 or len(month.manual_document_ref.strip()) < 5:
                raise ValueError("Ручной сумме нужны причина и документ-основание")
            target = _required(month.target_net_byn, "ручная месячная сумма на руки")
            rates = _rates(month)
            gross = _find_gross(
                target, Decimal(month.tax_deduction_byn),
                Decimal(month.other_withholding_byn),
                rates[0] / 100,
                rates[1] / 100,
            )
            if gross < salary:
                raise ValueError("Ручная цель ниже обязательного оклада")
            bonus = gross - salary
            selected = _breakdown(gross, month)
            row.update(manual=True, manual_reason=month.manual_reason.strip(),
                       manual_document_ref=month.manual_document_ref.strip())
        else:
            assert policy is not None
            selected = policy
        row.update(status="ready", salary_byn=_fmt(salary), bonus_byn=_fmt(bonus),
                   tax_deduction_byn=_fmt(Decimal(month.tax_deduction_byn)), **selected)
        return PayrollPolicyRowOut(**row), next_carry
    except ValueError as exc:
        row["error"] = str(exc)
        return PayrollPolicyRowOut(**row), None if employee.role == "seller" else carry


def calculate_payroll_policy_preview(payload: PayrollPolicyPreviewIn, *, can_manual: bool) -> PayrollPolicyPreviewOut:
    """Calculate no writes; director authorization is checked by the HTTP route."""
    with localcontext() as ctx:
        ctx.prec = 32
        rows: list[PayrollPolicyRowOut] = []
        seen: set[tuple[int, str]] = set()
        seen_employees: set[int] = set()
        for employee in payload.employees:
            if employee.employee_id in seen_employees:
                raise ValueError("Сотрудник должен быть одной записью с последовательными месяцами")
            seen_employees.add(employee.employee_id)
            carry: Decimal | None = None
            previous: str | None = None
            for index, month in enumerate(employee.months):
                key = employee.employee_id, month.period
                if key in seen or (previous is not None and month.period <= previous):
                    raise ValueError("Периоды сотрудника должны быть уникальны и по возрастанию")
                seen.add(key)
                if previous is not None and employee.role == "seller":
                    y, m = map(int, previous.split("-"))
                    expected = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
                    if month.period != expected:
                        carry = None
                row, carry = _month(employee, month, carry, index == 0)
                rows.append(row)
                previous = month.period
        return PayrollPolicyPreviewOut(
            can_manual_adjust=can_manual, rows=rows,
            warnings=[
                "Только черновик: документы, трудовые условия, льготы, налоговые пороги и применимость ставок не проверены сервером.",
                "Суммы отгрузок, возвратов, себестоимости и штрафов введены вручную с основанием, не подтверждены автоматической связью с ERP.",
                "ФИО и ID сотрудника в черновике не сверены с кадровой записью сервером.",
                "Взносы нанимателя по явно заданным ставкам ФСЗН и Белгосстраха не уменьшают сумму на руки.",
            ],
        )
