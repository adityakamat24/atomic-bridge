from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import AppContainer, get_container
from src.api.types import (
    PersonaSummary,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionState,
)
from src.core.data_store import Filter
from src.guardrails.view_scope import derive_role

router = APIRouter(prefix="/v1/session")
personas_router = APIRouter(prefix="/v1")


@router.post("")
async def create_session(
    body: SessionCreateRequest | None = None,
    container: AppContainer = Depends(get_container),
) -> SessionCreateResponse:
    req = body or SessionCreateRequest()
    user_sys_id = req.as_user_sys_id
    actor_name: str | None = None
    group_sys_ids: list[str] = []
    direct_report_sys_ids: list[str] = []
    managed_group_sys_ids: list[str] = []
    visible_user_sys_ids: list[str] = []

    if user_sys_id:
        user = container.store.get("sys_user", user_sys_id)
        if user is None:
            raise HTTPException(
                status_code=400,
                detail=f"as_user_sys_id {user_sys_id!r} not found",
            )
        actor_name = str(user.get("name") or "")
        # Explicit role overrides derivation.
        role = req.role or derive_role(user, container.store)

        if role != "admin":
            groups = user.get("member_groups") or []
            if isinstance(groups, list):
                group_sys_ids = [str(g) for g in groups]

            reports = container.store.find(
                "sys_user",
                [Filter(field="manager", operator="eq", value=user_sys_id)],
                limit=1000,
            )
            direct_report_sys_ids = [str(r["sys_id"]) for r in reports]

            managed = container.store.find(
                "sys_user_group",
                [Filter(field="manager", operator="eq", value=user_sys_id)],
                limit=1000,
            )
            managed_group_sys_ids = [str(g["sys_id"]) for g in managed]

            visible = {user_sys_id}
            own_manager = user.get("manager")
            if own_manager:
                visible.add(str(own_manager))
            if role == "agent" and group_sys_ids:
                all_users = container.store.find("sys_user", [], limit=10000)
                wanted = set(group_sys_ids)
                for u in all_users:
                    mg = u.get("member_groups") or []
                    if isinstance(mg, list) and any(g in wanted for g in mg):
                        visible.add(str(u["sys_id"]))
            elif role == "manager":
                visible.update(direct_report_sys_ids)
            visible_user_sys_ids = sorted(visible)
    else:
        role = req.role or "admin"

    state = await container.session_store.create(
        role=role,
        user_sys_id=user_sys_id,
        actor_name=actor_name,
        group_sys_ids=group_sys_ids,
        direct_report_sys_ids=direct_report_sys_ids,
        managed_group_sys_ids=managed_group_sys_ids,
        visible_user_sys_ids=visible_user_sys_ids,
    )
    return SessionCreateResponse(
        session_id=state.session_id,
        role=state.role,
        actor_name=state.actor_name,
        user_sys_id=state.user_sys_id,
    )


@router.get("/{sid}")
async def get_session(
    sid: str, container: AppContainer = Depends(get_container)
) -> SessionState:
    state = await container.session_store.get(sid)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return state


@router.delete("/{sid}")
async def delete_session(
    sid: str, container: AppContainer = Depends(get_container)
) -> dict[str, str]:
    await container.session_store.delete(sid)
    return {"status": "deleted"}


@personas_router.get("/personas")
async def list_personas(
    container: AppContainer = Depends(get_container),
) -> list[PersonaSummary]:
    """Synthetic admin row first, then every active user with their
    derived role + summary metadata for the picker."""
    users = container.store.find("sys_user", [], limit=10000)

    out: list[PersonaSummary] = [
        PersonaSummary(
            sys_id=None,
            role="admin",
            name="Admin",
            department="full access (no actor)",
            is_synthetic_admin=True,
        )
    ]

    all_users = users
    all_groups = container.store.find("sys_user_group", [], limit=10000)
    reports_by_manager: dict[str, int] = {}
    for u in all_users:
        mgr = u.get("manager")
        if mgr:
            reports_by_manager[str(mgr)] = reports_by_manager.get(str(mgr), 0) + 1
    managed_by_user: dict[str, int] = {}
    for g in all_groups:
        mgr = g.get("manager")
        if mgr:
            managed_by_user[str(mgr)] = managed_by_user.get(str(mgr), 0) + 1

    for u in users:
        if u.get("active") is False:
            continue
        sys_id = str(u.get("sys_id") or "")
        role = derive_role(u, container.store)
        member_groups = u.get("member_groups") or []
        out.append(
            PersonaSummary(
                sys_id=sys_id,
                role=role,
                name=str(u.get("name") or sys_id),
                department=str(u.get("department") or ""),
                member_groups=[str(g) for g in member_groups]
                if isinstance(member_groups, list)
                else [],
                direct_report_count=reports_by_manager.get(sys_id, 0),
                managed_group_count=managed_by_user.get(sys_id, 0),
            )
        )
    return out
