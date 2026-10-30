"""Worktime endpoints own their transactions and authorize actual employees."""
from fastapi import APIRouter, Depends, Response

from core.runtime.deps import get_core, get_session
from core.services.auth import get_current_user
from modules.hr import worktime, worktime_reminders

router = APIRouter(tags=["worktime"])


def no_store(response):
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/me/worktime/notices")
async def notices(response: Response, actor=Depends(get_current_user),
                  session=Depends(get_session, scope="function")):
    no_store(response)
    return await worktime_reminders.notices(session, actor)


@router.get("/me/worktime")
async def context(response: Response, actor=Depends(get_current_user),
                  session=Depends(get_session, scope="function")):
    no_store(response)
    return await worktime.context(session, actor)


@router.post("/me/worktime/start")
async def start(data: worktime.Command, response: Response, actor=Depends(get_current_user),
                session=Depends(get_session, scope="function"), core=Depends(get_core)):
    no_store(response)
    result = await worktime.start(session, actor, data, core.services.event_bus)
    await session.commit()
    return result


@router.post("/me/worktime/presence")
async def presence_snapshot(response: Response, actor=Depends(get_current_user),
                            session=Depends(get_session, scope="function")):
    no_store(response)
    result = await worktime.presence(session, actor)
    await session.commit()
    return result


@router.post("/me/worktime/{session_id}/presence")
async def presence(session_id: int, response: Response, actor=Depends(get_current_user),
                   session=Depends(get_session, scope="function")):
    no_store(response)
    result = await worktime.heartbeat(session, actor, session_id)
    await session.commit()
    return result


@router.post("/me/worktime/end")
async def end(data: worktime.EndCommand, response: Response, actor=Depends(get_current_user),
              session=Depends(get_session, scope="function"), core=Depends(get_core)):
    no_store(response)
    result = await worktime.end(session, actor, data, core.services.approvals, core.services.event_bus)
    await session.commit()
    return result


@router.get("/worktime/board")
async def board(response: Response, actor=Depends(get_current_user),
                session=Depends(get_session, scope="function")):
    no_store(response)
    return await worktime.board(session, actor)


@router.get("/worktime/timesheet")
async def timesheet(period: str, response: Response, actor=Depends(get_current_user),
                    session=Depends(get_session, scope="function")):
    no_store(response)
    return await worktime.timesheet(session, actor, period)


@router.get("/worktime/settings")
async def settings(response: Response, actor=Depends(get_current_user),
                   session=Depends(get_session, scope="function")):
    no_store(response)
    return await worktime.settings(session, actor)


@router.post("/worktime/policies")
async def configure(data: worktime.PolicyInput, response: Response, actor=Depends(get_current_user),
                    session=Depends(get_session, scope="function")):
    no_store(response)
    result = await worktime.configure(session, actor, data)
    await session.commit()
    return result


@router.post("/worktime/absences")
async def absence(data: worktime.AbsenceInput, response: Response, actor=Depends(get_current_user),
                  session=Depends(get_session, scope="function")):
    no_store(response)
    result = await worktime.absence(session, actor, data)
    await session.commit()
    return result


@router.post("/worktime/days/{day_id}/decision")
async def decide(day_id: int, data: worktime.DecisionInput, response: Response,
                 actor=Depends(get_current_user), session=Depends(get_session, scope="function"),
                 core=Depends(get_core)):
    no_store(response)
    result = await worktime.decide(session, actor, day_id, data, core.services.approvals)
    await session.commit()
    return result
