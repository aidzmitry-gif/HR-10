"""ORM-модели модуля HR (схема ``hr.*``)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base


class Employee(Base):
    """Сотрудник: ФИО, должность, отдел, статус."""

    __tablename__ = "employee"
    __table_args__ = {"schema": "hr"}

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[str] = mapped_column(String(128), default="", server_default="")
    department: Mapped[str] = mapped_column(String(128), default="", server_default="")
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EmployeeProfile(Base):
    """Private self-service fields; never serialized through EmployeeOut."""

    __tablename__ = "employee_profile"
    __table_args__ = {"schema": "hr"}

    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), primary_key=True)
    birth_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String(40), default="", server_default="")
    city: Mapped[str] = mapped_column(String(128), default="", server_default="")
    education: Mapped[str] = mapped_column(String(500), default="", server_default="")
    children_birth_dates: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Candidate(Base):
    """Кандидат в воронке подбора: от новой вакансии до найма.

    Стадия (`stage`) ведёт кандидата по этапам подбора (см. ``stages.py``):
    вакансия → приглашение → тех. интервью → оффер → нанят.
    """

    __tablename__ = "candidate"
    __table_args__ = {"schema": "hr"}

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(64), default="", server_default="")
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    position: Mapped[str] = mapped_column(String(128), default="", server_default="")
    salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), server_default="0")
    recruiter: Mapped[str] = mapped_column(String(128), default="", server_default="")
    priority: Mapped[str] = mapped_column(String(32), default="Средний", server_default="Средний")
    stage: Mapped[str] = mapped_column(String(32), default="new", server_default="new")
    next_step: Mapped[str] = mapped_column(String(255), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PayrollEntry(Base):
    """Начисление/выплата зарплаты сотруднику за период."""

    __tablename__ = "payroll_entry"
    __table_args__ = {"schema": "hr"}

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"))
    period: Mapped[str] = mapped_column(String(7))  # "YYYY-MM"
    amount_byn: Mapped[str] = mapped_column(String(20))  # Decimal as str
    # ponytail: статус — строка, не enum; апгрейд до Enum если стадий станет >2
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OkkScore(Base):
    """ОКК-оценка сотрудника за период: дисциплина, качество, сервис, командная."""

    __tablename__ = "okk_score"
    __table_args__ = {"schema": "hr"}

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"))
    period: Mapped[str] = mapped_column(String(7))  # "YYYY-MM"
    discipline: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0-25
    quality: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0-25
    service: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0-25
    teamwork: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0-25
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # сумма 0-100
    comment: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WorktimeReminder(Base):
    """Durable employee reminder; delivery never changes the employee's time."""

    __tablename__ = "worktime_reminder"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", "kind", name="uq_worktime_reminder_employee_day_kind"),
        CheckConstraint("kind IN ('start', 'end')", name="worktime_reminder_kind"),
        {"schema": "hr"},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(5))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("hr.work_session.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkPresence(Base):
    """Latest visible ERP heartbeat; never used for credited working hours."""

    __tablename__ = "work_presence"
    __table_args__ = {"schema": "hr"}
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorktimePolicy(Base):
    """Immutable effective contract norm and the employee's designated approver."""

    __tablename__ = "worktime_policy"
    __table_args__ = (
        UniqueConstraint("employee_id", "effective_from"),
        CheckConstraint("daily_minutes > 0 AND daily_minutes <= 1440", name="worktime_norm"),
        CheckConstraint("weekmask > 0 AND weekmask <= 127", name="worktime_weekmask"),
        CheckConstraint("start_minute IS NULL OR (start_minute >= 0 AND start_minute < 1440)", name="worktime_start_minute"),
        {"schema": "hr"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    daily_minutes: Mapped[int] = mapped_column(Integer)
    weekmask: Mapped[int] = mapped_column(Integer)
    start_minute: Mapped[int | None] = mapped_column(Integer)
    manager_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"))
    reference: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkSession(Base):
    """An explicit button-to-button shift; presence never changes credited time."""

    __tablename__ = "work_session"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_work_session_employee_date"),
        UniqueConstraint("employee_id", "start_key", name="uq_work_session_employee_start_key"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="work_session_time"),
        CheckConstraint("last_seen_at >= started_at", name="work_session_presence"),
        CheckConstraint("(ended_at IS NULL) = (end_key IS NULL)", name="work_session_end_key"),
        Index("uq_work_session_open_employee", "employee_id", unique=True,
              postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
        {"schema": "hr"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("hr.worktime_policy.id"))
    work_date: Mapped[date] = mapped_column(Date)
    start_key: Mapped[str] = mapped_column(String(36))
    end_key: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorktimeDay(Base):
    """An immutable day result, with extra hours controlled by a core approval."""

    __tablename__ = "worktime_day"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date"),
        UniqueConstraint("absence_key"),
        CheckConstraint("worked_seconds >= 0 AND worked_seconds <= 86400", name="worktime_seconds"),
        CheckConstraint("norm_minutes >= 0 AND norm_minutes <= 1440", name="worktime_day_norm"),
        CheckConstraint("code IS NULL OR code IN ('В', 'О', 'Б', 'К', 'А')", name="worktime_day_code"),
        CheckConstraint("(session_id IS NOT NULL AND code IS NULL AND absence_key IS NULL) OR "
                        "(session_id IS NULL AND code IS NOT NULL AND absence_key IS NOT NULL AND worked_seconds = 0)",
                        name="worktime_day_source"),
        {"schema": "hr"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("hr.employee.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("hr.worktime_policy.id"))
    work_date: Mapped[date] = mapped_column(Date)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("hr.work_session.id"))
    worked_seconds: Mapped[int] = mapped_column(Integer)
    norm_minutes: Mapped[int] = mapped_column(Integer)
    code: Mapped[str | None] = mapped_column(String(1))
    absence_key: Mapped[str | None] = mapped_column(String(36))
    approval_id: Mapped[int | None] = mapped_column(ForeignKey("approval.id"), unique=True)
    reference: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
