"""Explicit workday commands. Presence is informational; payroll is never posted."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from config.access import is_super
from core.domain.models import Approval, AuditLog, User
from modules.hr.models import Employee, WorkPresence, WorkSession, WorktimeDay, WorktimePolicy
from modules.hr.profile_routes import own_employee

ZONE = ZoneInfo("Europe/Minsk")
APPROVAL_KIND = "module:hr.worktime.overtime"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Command(Input):
    request_key: UUID
    employee_id: int = Field(gt=0, strict=True)


class EndCommand(Command):
    session_id: int = Field(gt=0, strict=True)


class PolicyInput(Input):
    employee_id: int = Field(gt=0, strict=True)
    effective_from: date
    daily_minutes: int = Field(gt=0, le=1440, strict=True)
    weekmask: int = Field(gt=0, le=127, strict=True)
    start_minute: int | None = Field(default=None, ge=0, lt=1440, strict=True)
    manager_id: int = Field(gt=0, strict=True)
    reference: str = Field(min_length=5, max_length=300)


class AbsenceInput(Command):
    employee_id: int = Field(gt=0, strict=True)
    work_date: date
    code: Literal["В", "О", "Б", "К", "А"]
    reference: str = Field(min_length=5, max_length=300)


class DecisionInput(Input):
    approved: bool = Field(strict=True)
    reason: str = Field(min_length=3, max_length=500)


def now():
    return datetime.now(UTC)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def fail(message, status=409):
    raise HTTPException(status, message)


def administrator(actor, user):
    # The identity row is locked before checking a management capability.
    # Revocation while a request waits must not leave an old token authoritative.
    return ("hr" in actor.roles or is_super(actor.roles)) and (
        user.role == "hr" or is_super([user.role] if user.role else []))


async def actor_user(session, actor):
    if not actor.roles or set(actor.roles) & {"Гость", "onboarding", "identity_provisioner", "crm_invitation_operator"}:
        fail("Учёт времени недоступен для этой учётной записи", 403)
    predicate = User.keycloak_user_id == actor.keycloak_user_id if actor.keycloak_user_id else User.username == actor.username
    row = await session.scalar(select(User).where(predicate).with_for_update())
    if row is None or row.status != "active":
        fail("Нужна действующая учётная запись сотрудника", 403)
    return row


async def own(session, actor):
    user = await actor_user(session, actor)
    employee = await own_employee(actor, session)
    return user, employee


async def policy_on(session, employee_id, day, required=True):
    row = await session.scalar(select(WorktimePolicy).where(
        WorktimePolicy.employee_id == employee_id, WorktimePolicy.effective_from <= day,
    ).order_by(WorktimePolicy.effective_from.desc()).limit(1))
    if row is None and required:
        fail("Норма и руководитель по договору ещё не настроены. Обратитесь в HR")
    return row


def policy_view(row):
    return None if row is None else {key: getattr(row, key) for key in (
        "id", "employee_id", "effective_from", "daily_minutes", "weekmask", "start_minute", "manager_id", "reference",
    )}


def employee_view(row):
    return {key: getattr(row, key) for key in ("id", "full_name", "department", "position", "status")}


def session_view(row, observed):
    if row is None:
        return None
    seconds = max(0, int(((utc(row.ended_at) if row.ended_at else observed) - utc(row.started_at)).total_seconds()))
    return {"id": row.id, "employee_id": row.employee_id, "work_date": row.work_date, "started_at": utc(row.started_at),
            "ended_at": utc(row.ended_at) if row.ended_at else None, "elapsed_seconds": seconds,
            "present": row.ended_at is None and 0 <= (observed - utc(row.last_seen_at)).total_seconds() <= 90,
            "last_seen_at": utc(row.last_seen_at), "source": "explicit_buttons"}


def audit(session, actor, action, entity, detail=None):
    session.add(AuditLog(actor=actor.username, action="hr.worktime." + action,
                        entity_ref=entity, detail=detail or {}))


async def context(session, actor):
    user, employee = await own(session, actor)
    observed = now()
    day = observed.astimezone(ZONE).date()
    current = await session.scalar(select(WorkSession).where(
        WorkSession.employee_id == employee.id,
        (WorkSession.ended_at.is_(None)) | (WorkSession.work_date == day),
    ).order_by(WorkSession.id.desc()).limit(1))
    policy = await policy_on(session, employee.id, day, required=False)
    return {"server_now": observed, "today": day, "timezone": ZONE.key, "employee": employee_view(employee),
            "session": session_view(current, observed), "policy": policy_view(policy),
            "can_configure": administrator(actor, user), "user_id": user.id,
            "time_rule": "between_buttons", "presence_affects_hours": False}


async def start(session, actor, command, event_bus):
    _, employee = await own(session, actor)
    if command.employee_id != employee.id:
        fail("Учётная запись изменилась. Обновите страницу перед началом дня", 403)
    key = str(command.request_key)
    replay = await session.scalar(select(WorkSession).where(
        WorkSession.employee_id == employee.id, WorkSession.start_key == key))
    if replay is not None:
        return session_view(replay, now())
    observed = now()
    day = observed.astimezone(ZONE).date()
    if await session.scalar(select(WorkSession.id).where(WorkSession.employee_id == employee.id,
            (WorkSession.ended_at.is_(None)) | (WorkSession.work_date == day)).limit(1)):
        fail("Рабочий день уже начат или завершён. Обновите состояние")
    if await session.scalar(select(WorktimeDay.id).where(
            WorktimeDay.employee_id == employee.id, WorktimeDay.work_date == day)):
        fail("На этот день уже есть запись табеля")
    policy = await policy_on(session, employee.id, day)
    row = WorkSession(employee_id=employee.id, policy_id=policy.id, work_date=day,
                      start_key=key, started_at=observed, last_seen_at=observed)
    session.add(row)
    await session.flush()
    audit(session, actor, "started", f"work-session:{row.id}")
    event_bus.emit(session, "hr.workday.started", {"employee_id": employee.id, "session_id": row.id})
    return session_view(row, observed)


async def heartbeat(session, actor, session_id):
    _, employee = await own(session, actor)
    row = await session.scalar(select(WorkSession).where(
        WorkSession.id == session_id, WorkSession.employee_id == employee.id).with_for_update())
    if row is None:
        fail("Рабочий день не найден", 404)
    observed = now()
    if row.ended_at is None:
        row.last_seen_at = max(utc(row.last_seen_at), observed)
        await session.flush()
    return session_view(row, observed)


async def presence(session, actor):
    _, employee = await own(session, actor)
    observed = now()
    row = await session.get(WorkPresence, employee.id)
    if row is None:
        session.add(WorkPresence(employee_id=employee.id, last_seen_at=observed))
    else:
        row.last_seen_at = max(utc(row.last_seen_at), observed)
    await session.flush()
    return {"employee_id": employee.id, "last_seen_at": observed, "presence_affects_hours": False}


def day_parts(started, ended):
    cursor = utc(started)
    ended = utc(ended)
    if ended < cursor:
        fail("Время сервера предшествует началу дня")
    if ended - cursor > timedelta(days=366):
        fail("Слишком длинная незакрытая смена: требуется проверка HR")
    parts = []
    while cursor < ended or not parts:
        day = cursor.astimezone(ZONE).date()
        boundary = datetime.combine(day + timedelta(days=1), time.min, ZONE).astimezone(UTC)
        stop = min(ended, boundary)
        parts.append((day, max(0, int((stop - cursor).total_seconds()))))
        cursor = stop
    return parts


async def end(session, actor, command, approvals, event_bus):
    _, employee = await own(session, actor)
    if command.employee_id != employee.id:
        fail("Учётная запись изменилась. Обновите страницу перед завершением дня", 403)
    row = await session.scalar(select(WorkSession).where(
        WorkSession.id == command.session_id, WorkSession.employee_id == employee.id).with_for_update())
    if row is None:
        fail("Рабочий день не найден", 404)
    key = str(command.request_key)
    if row.ended_at is not None:
        if row.end_key != key:
            fail("Рабочий день уже завершён другим запросом")
        return session_view(row, now())
    observed = now()
    for day, seconds in day_parts(row.started_at, observed):
        if await session.scalar(select(WorktimeDay.id).where(
                WorktimeDay.employee_id == employee.id, WorktimeDay.work_date == day)):
            fail("Запись табеля уже существует: требуется сверка HR")
        policy = await policy_on(session, employee.id, day)
        norm = policy.daily_minutes if policy.weekmask & (1 << day.weekday()) else 0
        approval_id = None
        if Decimal(seconds // 3600) > Decimal(norm) / 60:
            manager = await session.get(User, policy.manager_id)
            if manager is None or manager.status != "active" or manager.employee_id == employee.id:
                fail("Руководитель для согласования недоступен. Обратитесь в HR")
            approval = await approvals.request(session, APPROVAL_KIND,
                f"work-session:{row.id}:{day.isoformat()}", f"Переработка сотрудника №{employee.id}: {day}",
                actor.username, route_override=f"user:{manager.username}")
            approval_id = approval.id
        session.add(WorktimeDay(employee_id=employee.id, policy_id=policy.id, work_date=day,
            session_id=row.id, worked_seconds=seconds, norm_minutes=norm,
            approval_id=approval_id, reference=policy.reference, created_by=actor.username))
    row.ended_at, row.end_key = observed, key
    await session.flush()
    audit(session, actor, "ended", f"work-session:{row.id}")
    event_bus.emit(session, "hr.workday.ended", {"employee_id": employee.id, "session_id": row.id})
    return session_view(row, observed)


async def scoped_employees(session, actor, user, day):
    if administrator(actor, user):
        return list((await session.scalars(select(Employee).order_by(Employee.full_name))).all())
    policies = (await session.scalars(select(WorktimePolicy).where(
        WorktimePolicy.effective_from <= day).order_by(WorktimePolicy.effective_from.desc()))).all()
    latest = {}
    for row in policies:
        latest.setdefault(row.employee_id, row)
    ids = {p.employee_id for p in latest.values() if p.manager_id == user.id}
    if user.employee_id:
        ids.add(user.employee_id)
    return list((await session.scalars(select(Employee).where(Employee.id.in_(ids))
                                     .order_by(Employee.full_name))).all())


async def board(session, actor):
    user = await actor_user(session, actor)
    observed = now()
    day = observed.astimezone(ZONE).date()
    rows = []
    for employee in await scoped_employees(session, actor, user, day):
        if employee.status != "active":
            continue
        current = await session.scalar(select(WorkSession).where(
            WorkSession.employee_id == employee.id,
            (WorkSession.ended_at.is_(None)) | (WorkSession.work_date == day),
        ).order_by(WorkSession.id.desc()).limit(1))
        seen = await session.get(WorkPresence, employee.id)
        present = bool(seen and 0 <= (observed - utc(seen.last_seen_at)).total_seconds() <= 90)
        rows.append({"employee": employee_view(employee), "session": session_view(current, observed),
                     "present": present, "last_seen_at": utc(seen.last_seen_at) if seen else None})
    return {"server_now": observed, "items": rows, "presence_affects_hours": False}


async def configure(session, actor, data):
    user = await actor_user(session, actor)
    if not administrator(actor, user):
        fail("Нормы по договорам настраивает HR или директор", 403)
    employee = await session.scalar(select(Employee).where(
        Employee.id == data.employee_id, Employee.status == "active").with_for_update())
    manager = await session.get(User, data.manager_id)
    if employee is None or manager is None or manager.status != "active" or not manager.employee_id:
        fail("Сотрудник или руководитель недоступен", 422)
    if manager.employee_id == employee.id:
        fail("Сотрудник не может согласовывать собственную переработку", 422)
    existing = await session.scalar(select(WorktimePolicy).where(
        WorktimePolicy.employee_id == employee.id, WorktimePolicy.effective_from == data.effective_from))
    if existing is not None:
        if all(getattr(existing, key) == value for key, value in data.model_dump().items()):
            return policy_view(existing)
        fail("Норма на эту дату уже сохранена; задайте новую дату действия")
    row = WorktimePolicy(**data.model_dump(), created_by=actor.username)
    session.add(row)
    await session.flush()
    audit(session, actor, "policy_created", f"employee:{employee.id}", {"policy_id": row.id})
    return policy_view(row)


async def settings(session, actor):
    user = await actor_user(session, actor)
    if not administrator(actor, user):
        fail("Настройки норм доступны HR или директору", 403)
    employees = (await session.scalars(select(Employee).where(Employee.status == "active").order_by(Employee.full_name))).all()
    users = (await session.scalars(select(User).join(Employee, Employee.id == User.employee_id).where(
        User.status == "active", Employee.status == "active").order_by(User.full_name))).all()
    return {"employees": [employee_view(e) for e in employees],
            "managers": [{"id": u.id, "full_name": u.full_name, "employee_id": u.employee_id} for u in users]}


async def absence(session, actor, data):
    user = await actor_user(session, actor)
    if not administrator(actor, user):
        fail("Коды отсутствия вносит HR или директор", 403)
    if data.work_date > now().astimezone(ZONE).date():
        fail("Будущие дни подтверждаются после наступления даты", 422)
    employee = await session.scalar(select(Employee).where(Employee.id == data.employee_id).with_for_update())
    if employee is None:
        fail("Сотрудник не найден", 404)
    replay = await session.scalar(select(WorktimeDay).where(WorktimeDay.absence_key == str(data.request_key)))
    if replay is not None:
        if (replay.employee_id, replay.work_date, replay.code, replay.reference) != (
                employee.id, data.work_date, data.code, data.reference):
            fail("Ключ уже использован для другой записи")
        return {"id": replay.id}
    if await session.scalar(select(WorkSession.id).where(WorkSession.employee_id == employee.id,
            WorkSession.ended_at.is_(None), WorkSession.work_date <= data.work_date).limit(1)):
        fail("У сотрудника не завершена смена")
    if await session.scalar(select(WorktimeDay.id).where(
            WorktimeDay.employee_id == employee.id, WorktimeDay.work_date == data.work_date)):
        fail("На этот день уже есть запись табеля")
    policy = await policy_on(session, employee.id, data.work_date)
    row = WorktimeDay(employee_id=employee.id, policy_id=policy.id, work_date=data.work_date,
        worked_seconds=0, norm_minutes=policy.daily_minutes if policy.weekmask & (1 << data.work_date.weekday()) else 0,
        code=data.code, absence_key=str(data.request_key), reference=data.reference, created_by=actor.username)
    session.add(row)
    await session.flush()
    audit(session, actor, "absence_recorded", f"worktime-day:{row.id}")
    return {"id": row.id}


async def decide(session, actor, day_id, data, approvals):
    user = await actor_user(session, actor)
    day = await session.get(WorktimeDay, day_id)
    if day is None:
        fail("Запись не найдена", 404)
    await session.scalar(select(Employee).where(Employee.id == day.employee_id).with_for_update())
    policy = await session.get(WorktimePolicy, day.policy_id)
    if policy.manager_id != user.id or user.employee_id == day.employee_id:
        fail("Переработку согласовывает назначенный руководитель", 403)
    approval = await session.scalar(select(Approval).where(Approval.id == day.approval_id).with_for_update())
    if approval is None or approval.kind != APPROVAL_KIND:
        fail("Запрос на переработку не найден")
    target = "approved" if data.approved else "rejected"
    if approval.status != "pending":
        if approval.status == target and approval.decided_by == actor.username:
            return {"id": day.id, "status": target}
        fail("Согласование уже обработано")
    await approvals.decide(session, approval, data.approved, actor.username, data.reason,
                           workflow_kind=APPROVAL_KIND)
    audit(session, actor, "overtime_decided", f"worktime-day:{day.id}", {"status": target})
    return {"id": day.id, "status": target}


async def timesheet(session, actor, period):
    try:
        first = date.fromisoformat(period + "-01")
    except ValueError:
        fail("Укажите месяц YYYY-MM", 422)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    user = await actor_user(session, actor)
    employees = await scoped_employees(session, actor, user, now().astimezone(ZONE).date())
    ids = {e.id for e in employees}
    # A former designated approver retains only the days assigned to them;
    # changing the current contract must not hide a pending historical decision.
    query = select(WorktimeDay).join(WorktimePolicy, WorktimePolicy.id == WorktimeDay.policy_id).where(
        WorktimeDay.work_date >= first, WorktimeDay.work_date < last)
    if not administrator(actor, user):
        query = query.where(WorktimeDay.employee_id.in_(ids) | (WorktimePolicy.manager_id == user.id))
    rows = (await session.scalars(query.order_by(WorktimeDay.work_date))).all()
    historical_ids = {row.employee_id for row in rows} - ids
    if historical_ids:
        employees.extend((await session.scalars(select(Employee).where(Employee.id.in_(historical_ids)))).all())
    items = []
    for row in rows:
        approval = await session.get(Approval, row.approval_id) if row.approval_id else None
        policy = await session.get(WorktimePolicy, row.policy_id)
        worked = Decimal(row.worked_seconds // 3600)
        norm = Decimal(row.norm_minutes) / 60
        extra = max(Decimal(0), worked - norm)
        total = min(worked, norm) + (extra if approval and approval.status == "approved" else Decimal(0))
        items.append({"id": row.id, "employee_id": row.employee_id, "date": row.work_date,
            "worked_seconds": row.worked_seconds, "norm_minutes": row.norm_minutes, "hours": format(total, "f"),
            "credited_minutes": int(total * 60), "extra_minutes": int(extra * 60),
            "code": row.code, "extra_hours": format(extra, "f"),
            "approval_status": approval.status if approval else None,
            "can_decide": bool(approval and approval.status == "pending" and policy.manager_id == user.id
                               and user.employee_id != row.employee_id), "source": "absence" if row.code else "explicit_buttons"})
    return {"period": period, "timezone": ZONE.key, "employees": [employee_view(e) for e in employees],
            "items": items, "can_configure": administrator(actor, user), "payroll_posted": False}
