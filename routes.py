"""HTTP-API модуля HR. Монтируется под префиксом ``/hr``."""
from __future__ import annotations

from decimal import Decimal
from itertools import groupby

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime.core import Core
from core.runtime.deps import get_core, get_session
from core.runtime.funnel import FunnelBoardOut, FunnelCard, build_board
from core.services.auth import GUEST, CurrentUser, get_current_user
from modules.hr.models import (
    Candidate,
    Employee,
    EmployeeProfile,
    OkkScore,
    PayrollEntry,
)
from modules.hr.payroll_policy import calculate_payroll_policy_preview
from modules.hr.payroll_preview import calculate_payroll_preview
from modules.hr.schemas import (
    CandidateCreate,
    CandidateOut,
    EmployeeCreate,
    EmployeeOut,
    OkkScoreCreate,
    OkkScoreOut,
    PayrollAccrueIn,
    PayrollEntryOut,
    PayrollPayIn,
    PayrollPeriodSummary,
    PayrollPolicyPreviewIn,
    PayrollPolicyPreviewOut,
    PayrollPreviewIn,
    PayrollPreviewOut,
    StageUpdate,
)
from modules.hr.stages import STAGES

router = APIRouter(tags=["hr"])


# --- Сотрудники (штат) ---


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(session: AsyncSession = Depends(get_session)):
    """Сотрудники."""
    rows = (await session.execute(
        select(Employee, EmployeeProfile.status).outerjoin(
            EmployeeProfile, EmployeeProfile.employee_id == Employee.id,
        ).order_by(Employee.id.desc())
    )).all()
    return [EmployeeOut.model_validate(employee).model_copy(update={
        "profile_status": profile_status or "not_started",
    }) for employee, profile_status in rows]


@router.post("/employees", response_model=EmployeeOut, status_code=201)
async def create_employee(payload: EmployeeCreate, session: AsyncSession = Depends(get_session)):
    """Добавить сотрудника."""
    obj = Employee(**payload.model_dump())
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


# --- Воронка подбора (кандидаты) ---


def _to_card(r: Candidate) -> FunnelCard:
    return FunnelCard(
        id=r.id,
        code=r.number or f"CAND-{r.id}",
        title=r.name,
        subtitle=r.position,
        amount=float(r.salary),
        priority=r.priority,
        owner=r.recruiter,
        next_step=r.next_step,
    )


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(session: AsyncSession = Depends(get_session)):
    """Кандидаты (плоский список)."""
    return (await session.execute(select(Candidate).order_by(Candidate.id.desc()))).scalars().all()


@router.get("/board", response_model=FunnelBoardOut)
async def board(session: AsyncSession = Depends(get_session)) -> FunnelBoardOut:
    """Воронка подбора: кандидаты сгруппированы по этапам найма."""
    rows = (await session.execute(select(Candidate))).scalars().all()
    return build_board(STAGES, rows, _to_card)


@router.post("/candidates", response_model=CandidateOut, status_code=201)
async def create_candidate(payload: CandidateCreate, session: AsyncSession = Depends(get_session)):
    """Создать кандидата. Номер генерируется автоматически, если не задан."""
    data = payload.model_dump()
    data["salary"] = Decimal(str(data["salary"]))
    obj = Candidate(**data)
    session.add(obj)
    await session.flush()
    if not obj.number:
        obj.number = f"CAND-2026-{obj.id:04d}"
    await session.commit()
    await session.refresh(obj)
    return obj


@router.patch("/candidates/{cand_id}", response_model=CandidateOut)
async def update_candidate(
    cand_id: int, payload: StageUpdate, session: AsyncSession = Depends(get_session)
):
    """Сменить этап подбора кандидата."""
    obj = await session.get(Candidate, cand_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    obj.stage = payload.stage
    await session.commit()
    await session.refresh(obj)
    return obj


# --- Зарплата (payroll) ---


@router.post("/payroll/preview", response_model=PayrollPreviewOut)
def payroll_preview(payload: PayrollPreviewIn):
    """Предпросмотр от месячной суммы на руки; без БД, начисления, выплаты и outbox."""
    try:
        return calculate_payroll_preview(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/payroll/policy-preview", response_model=PayrollPolicyPreviewOut)
def payroll_policy_preview(
    payload: PayrollPolicyPreviewIn,
    actor: CurrentUser = Depends(get_current_user),
):
    """Document-sourced draft only; no DB, accrual, payment or outbox."""
    if GUEST in actor.roles or not actor.roles:
        raise HTTPException(status_code=403, detail="Требуется роль ERP")
    can_manual = "director" in actor.roles
    if not can_manual and any(
        month.mode == "manual"
        for employee in payload.employees for month in employee.months
    ):
        raise HTTPException(status_code=403, detail="Ручная корректировка доступна только директору")
    try:
        return calculate_payroll_policy_preview(payload, can_manual=can_manual)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/payroll", response_model=list[PayrollEntryOut])
async def list_payroll(
    employee_id: int | None = None,
    period: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Список начислений с опциональными фильтрами."""
    q = select(PayrollEntry).order_by(PayrollEntry.id.desc())
    if employee_id is not None:
        q = q.where(PayrollEntry.employee_id == employee_id)
    if period is not None:
        q = q.where(PayrollEntry.period == period)
    if status is not None:
        q = q.where(PayrollEntry.status == status)
    return (await session.execute(q)).scalars().all()


@router.get("/payroll/summary", response_model=list[PayrollPeriodSummary])
async def payroll_summary(session: AsyncSession = Depends(get_session)):
    """Сводка начислений по периодам: итог BYN, кол-во, ожидает."""
    rows = (
        await session.execute(select(PayrollEntry).order_by(PayrollEntry.period.asc()))
    ).scalars().all()
    result: list[PayrollPeriodSummary] = []
    for period, group in groupby(rows, key=lambda r: r.period):
        items = list(group)
        total = sum(Decimal(r.amount_byn) for r in items)
        pending = sum(1 for r in items if r.status == "pending")
        result.append(PayrollPeriodSummary(
            period=period,
            total_byn=str(total),
            count=len(items),
            pending_count=pending,
        ))
    return result


@router.get("/payroll/{entry_id}", response_model=PayrollEntryOut)
async def get_payroll_entry(entry_id: int, session: AsyncSession = Depends(get_session)):
    """Одна запись начисления."""
    obj = await session.get(PayrollEntry, entry_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return obj


@router.post("/payroll/accrue", response_model=PayrollEntryOut, status_code=201)
async def payroll_accrue(
    payload: PayrollAccrueIn,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Начислить зарплату сотруднику за период.

    Создаёт PayrollEntry(status=pending) и эмитит hr.payroll.accrued в outbox.
    """
    emp = await session.get(Employee, payload.employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    entry = PayrollEntry(
        employee_id=payload.employee_id,
        period=payload.period,
        amount_byn=payload.amount_byn,
        status="pending",
    )
    session.add(entry)
    await session.flush()  # получаем entry.id

    core.event_bus.emit(
        session,
        "hr.payroll.accrued",
        {
            "employee_id": emp.id,
            "employee_name": emp.full_name,
            "period": payload.period,
            "amount_byn": payload.amount_byn,
            "entity_ref": f"payroll:{entry.id}",
        },
    )
    await session.commit()
    await session.refresh(entry)
    return entry


@router.post("/payroll/pay", response_model=PayrollEntryOut)
async def payroll_pay(
    payload: PayrollPayIn,
    core: Core = Depends(get_core),
    session: AsyncSession = Depends(get_session),
):
    """Отметить конкретную HR-запись выплаченной.

    Старый вызов без entry_id допускается только при единственной pending-записи
    сотрудника за период. Эта ручная отметка не подтверждает банковский платёж.
    """
    query = select(PayrollEntry).where(
        PayrollEntry.employee_id == payload.employee_id,
        PayrollEntry.period == payload.period,
        PayrollEntry.status == "pending",
    )
    if payload.entry_id is None:
        rows = (await session.execute(
            query.order_by(PayrollEntry.id).limit(2).with_for_update()
        )).scalars().all()
        if len(rows) > 1:
            raise HTTPException(status_code=409, detail="Укажите конкретное начисление")
        entry = rows[0] if rows else None
    else:
        entry = await session.scalar(
            query.where(PayrollEntry.id == payload.entry_id).with_for_update()
        )
    if entry is None:
        raise HTTPException(status_code=404, detail="Начисление не найдено или уже выплачено")

    entry.status = "paid"

    core.event_bus.emit(
        session,
        "hr.payroll.paid",
        {
            "employee_id": entry.employee_id,
            "period": entry.period,
            "amount_byn": entry.amount_byn,
            "entity_ref": f"payroll:{entry.id}",
        },
    )
    await session.commit()
    await session.refresh(entry)
    return entry


# --- ОКК-оценки ---


@router.get("/okk-scores", response_model=list[OkkScoreOut])
async def list_okk_scores(
    employee_id: int | None = None,
    period: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Список ОКК-оценок с опциональными фильтрами по сотруднику и периоду."""
    q = select(OkkScore).order_by(OkkScore.id.desc())
    if employee_id is not None:
        q = q.where(OkkScore.employee_id == employee_id)
    if period is not None:
        q = q.where(OkkScore.period == period)
    return (await session.execute(q)).scalars().all()


@router.post("/okk-scores", response_model=OkkScoreOut, status_code=201)
async def create_okk_score(
    payload: OkkScoreCreate,
    session: AsyncSession = Depends(get_session),
):
    """Создать ОКК-оценку. total = discipline + quality + service + teamwork."""
    data = payload.model_dump()
    data["total"] = data["discipline"] + data["quality"] + data["service"] + data["teamwork"]
    obj = OkkScore(**data)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/okk-scores/{score_id}", response_model=OkkScoreOut)
async def get_okk_score(score_id: int, session: AsyncSession = Depends(get_session)):
    """Одна ОКК-оценка по id."""
    obj = await session.get(OkkScore, score_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="ОКК-оценка не найдена")
    return obj
