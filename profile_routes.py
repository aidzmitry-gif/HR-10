"""An employee edits only the profile bound to their verified local identity."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.models import AuditLog, User
from core.runtime.deps import get_session
from core.services.auth import CurrentUser, get_current_user
from modules.hr.models import Employee, EmployeeProfile

router = APIRouter(tags=["employee-profile"])


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    birth_date: date | None = None
    phone: str = Field(default="", max_length=40)
    city: str = Field(default="", max_length=128)
    education: str = Field(default="", max_length=500)
    # None = not disclosed; [] = no children. Names are not collected.
    children_birth_dates: list[date] | None = Field(default=None, max_length=30)
    status: Literal["draft", "submitted"] = "draft"
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_dates(self):
        dates = ([self.birth_date] if self.birth_date else []) + (self.children_birth_dates or [])
        today = datetime.now(UTC).date()
        if any(value > today or value.year < 1900 for value in dates):
            raise ValueError("Укажите действительную дату рождения, не в будущем")
        if self.birth_date and any(value <= self.birth_date for value in self.children_birth_dates or []):
            raise ValueError("Дата рождения ребёнка должна быть позже даты рождения сотрудника")
        return self


class ProfileOut(BaseModel):
    employee_id: int
    full_name: str
    department: str
    position: str
    birth_date: date | None = None
    phone: str = ""
    city: str = ""
    education: str = ""
    children_birth_dates: list[date] | None = None
    status: str = "not_started"
    revision: int = 0


def output(employee: Employee, profile: EmployeeProfile | None) -> ProfileOut:
    data = {key: getattr(profile, key) for key in ProfileInput.model_fields} if profile else {}
    return ProfileOut(employee_id=employee.id, full_name=employee.full_name,
                      department=employee.department, position=employee.position, **data)


async def own_employee(actor: CurrentUser, session: AsyncSession) -> Employee:
    if not set(actor.roles).difference({"Гость"}) or set(actor.roles) & {
        "Гость", "identity_provisioner", "crm_invitation_operator",
    }:
        raise HTTPException(403, "Личная карточка недоступна")
    predicate = User.keycloak_user_id == actor.keycloak_user_id if actor.keycloak_user_id else User.username == actor.username
    user = (await session.execute(select(User).where(predicate).with_for_update())).scalar_one_or_none()
    if user is None or user.status not in {"active", "onboarding"} or not user.employee_id:
        raise HTTPException(403, "Учётная запись не связана с действующей карточкой сотрудника")
    # Lock the parent, including when the profile row does not exist yet.
    employee = (await session.execute(select(Employee).where(
        Employee.id == user.employee_id, Employee.status == "active",
    ).with_for_update())).scalar_one_or_none()
    if employee is None:
        raise HTTPException(403, "Карточка сотрудника недоступна")
    return employee


@router.get("/me/profile", response_model=ProfileOut)
async def read_own_profile(response: Response, actor: CurrentUser = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    employee = await own_employee(actor, session)
    return output(employee, await session.get(EmployeeProfile, employee.id))


@router.put("/me/profile", response_model=ProfileOut)
async def save_own_profile(payload: ProfileInput, response: Response,
                           actor: CurrentUser = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    employee = await own_employee(actor, session)
    profile = await session.get(EmployeeProfile, employee.id)
    if payload.revision != (profile.revision if profile else 0):
        raise HTTPException(409, "Карточка изменена в другом окне. Обновите страницу перед сохранением")
    if profile is None:
        profile = EmployeeProfile(employee_id=employee.id)
        session.add(profile)
    for key in ("birth_date", "phone", "city", "education", "status"):
        setattr(profile, key, getattr(payload, key))
    profile.children_birth_dates = (
        [value.isoformat() for value in payload.children_birth_dates]
        if payload.children_birth_dates is not None else None
    )
    profile.revision = payload.revision + 1
    profile.updated_at = datetime.now(UTC)
    # No personal field values in audit/outbox.
    session.add(AuditLog(actor=actor.username, action="hr.profile.self_updated",
                        entity_ref=f"employee:{employee.id}",
                        detail={"revision": profile.revision, "status": profile.status}))
    await session.commit()
    return output(employee, profile)


@router.get("/employee-profiles/{employee_id}", response_model=ProfileOut)
async def read_hr_profile(employee_id: int, response: Response,
                          actor: CurrentUser = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    # Module access/superuser privileges do not grant access to private fields.
    if "hr" not in actor.roles or set(actor.roles) & {"onboarding", "Гость"}:
        raise HTTPException(403, "Личные сведения доступны только сотруднику и HR")
    employee = await session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(404, "Сотрудник не найден")
    session.add(AuditLog(actor=actor.username, action="hr.profile.read",
                        entity_ref=f"employee:{employee_id}", detail={}))
    await session.commit()
    return output(employee, await session.get(EmployeeProfile, employee_id))
