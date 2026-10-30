"""Addressed in-ERP reminders through the existing core tick and outbox."""
from datetime import datetime, time, timedelta
from time import monotonic

from sqlalchemy import select

from core.domain.models import Approval, User
from modules.hr import worktime as w
from modules.hr.models import Employee, WorkSession, WorktimeDay, WorktimeReminder

_next_tick = 0.0


async def queue_due(session, services):
    observed = w.now()
    today = observed.astimezone(w.ZONE).date()
    # Same User -> Employee lock order as explicit workday commands. Each core
    # worker skips rows held by another transaction; the unique key is a backstop.
    users = (await session.scalars(select(User).where(User.status == "active", User.employee_id.is_not(None))
        .order_by(User.id).with_for_update(skip_locked=True))).all()
    for user in users:
        if user.role in {"onboarding", "Гость", "identity_provisioner", "crm_invitation_operator"}:
            continue
        employee = await session.scalar(select(Employee).where(Employee.id == user.employee_id,
            Employee.status == "active").with_for_update(skip_locked=True))
        if employee is None:
            continue
        current = await session.scalar(select(WorkSession).where(WorkSession.employee_id == employee.id,
            WorkSession.ended_at.is_(None)))
        policy = await w.policy_on(session, employee.id, today, required=False)
        kind, work_date, session_id = None, today, None
        if current is not None:
            start_policy = await w.policy_on(session, employee.id, current.work_date, required=False)
            if start_policy and observed >= w.utc(current.started_at) + timedelta(minutes=start_policy.daily_minutes):
                kind, work_date, session_id = "end", current.work_date, current.id
        elif policy and policy.start_minute is not None and policy.weekmask & (1 << today.weekday()):
            scheduled = datetime.combine(today, time.min, w.ZONE) + timedelta(minutes=policy.start_minute + 15)
            if observed >= scheduled and not await session.scalar(select(WorkSession.id).where(
                    WorkSession.employee_id == employee.id, WorkSession.work_date == today)):
                if not await session.scalar(select(WorktimeDay.id).where(
                        WorktimeDay.employee_id == employee.id, WorktimeDay.work_date == today)):
                    kind = "start"
        if kind is None or await session.scalar(select(WorktimeReminder.id).where(
                WorktimeReminder.employee_id == employee.id, WorktimeReminder.work_date == work_date,
                WorktimeReminder.kind == kind)):
            continue
        reminder = WorktimeReminder(employee_id=employee.id, work_date=work_date, kind=kind, session_id=session_id)
        session.add(reminder)
        await session.flush()
        services.event_bus.emit(session, "hr.worktime.reminder", {
            "employee_id": employee.id, "reminder_id": reminder.id, "kind": kind,
            "work_date": work_date.isoformat(), "channel": "erp", "hours_changed": False,
        })


async def tick(session, services):
    global _next_tick
    current = monotonic()
    if current < _next_tick:
        return
    # No independent worker or import-time task. A failed tick is retried by core.
    _next_tick = current + 60
    try:
        await queue_due(session, services)
    except Exception:
        _next_tick = 0
        raise


async def notices(session, actor):
    user, employee = await w.own(session, actor)
    observed = w.now()
    today = observed.astimezone(w.ZONE).date()
    items = []
    reminders = (await session.scalars(select(WorktimeReminder).where(
        WorktimeReminder.employee_id == employee.id).order_by(WorktimeReminder.id.desc()))).all()
    for reminder in reminders:
        if reminder.kind == "start":
            if reminder.work_date != today:
                continue
            policy = await w.policy_on(session, employee.id, today, required=False)
            if not policy or policy.start_minute is None or not policy.weekmask & (1 << today.weekday()):
                continue
            scheduled = datetime.combine(today, time.min, w.ZONE) + timedelta(minutes=policy.start_minute + 15)
            if observed < scheduled:
                continue
            if await session.scalar(select(WorkSession.id).where(WorkSession.employee_id == employee.id,
                    WorkSession.work_date == today)) or await session.scalar(select(WorktimeDay.id).where(
                    WorktimeDay.employee_id == employee.id, WorktimeDay.work_date == today)):
                continue
            title = "Рабочий день не начат. Нажмите «Начать день», когда приступите к работе."
        else:
            shift = await session.get(WorkSession, reminder.session_id)
            if shift is None or shift.ended_at is not None:
                continue
            policy = await w.policy_on(session, employee.id, shift.work_date, required=False)
            if not policy or observed < w.utc(shift.started_at) + timedelta(minutes=policy.daily_minutes):
                continue
            title = "Норма рабочего дня достигнута. Не забудьте завершить день после окончания работы."
        items.append({"id": f"reminder:{reminder.id}", "kind": reminder.kind,
                      "title": title, "href": "/erp/hr/worktime", "work_date": reminder.work_date})
    days = (await session.scalars(select(WorktimeDay).join(w.WorktimePolicy,
        w.WorktimePolicy.id == WorktimeDay.policy_id).join(Approval, Approval.id == WorktimeDay.approval_id).where(
        w.WorktimePolicy.manager_id == user.id, WorktimeDay.employee_id != employee.id,
        Approval.kind == w.APPROVAL_KIND, Approval.status == "pending").order_by(WorktimeDay.work_date))).all()
    for day in days:
        items.append({"id": f"overtime:{day.id}", "kind": "overtime",
            "title": f"Переработка сотрудника №{day.employee_id} за {day.work_date}: требуется ваше решение.",
            "href": "/erp/hr/worktime?tab=sheet&period=" + day.work_date.strftime("%Y-%m"), "work_date": day.work_date})
    return {"items": items, "channel": "erp", "server_now": observed, "hours_changed": False}
