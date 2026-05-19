"""Role-based view control: matrix + three enforcement layers
(planner-aware prompt block, validator rejection, executor row-filter).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.api.types import Role, SessionState
from src.core.data_store import Filter

if TYPE_CHECKING:
    from src.core.data_store import DataStore


def derive_role(user_record: dict[str, Any], store: DataStore) -> Role:
    """manager (has reports OR manages groups) > agent (has
    member_groups) > end_user. admin is never derived."""
    sys_id = user_record.get("sys_id")
    if not sys_id:
        return "end_user"
    reports = store.find(
        "sys_user",
        [Filter(field="manager", operator="eq", value=str(sys_id))],
        limit=1,
    )
    if reports:
        return "manager"
    managed = store.find(
        "sys_user_group",
        [Filter(field="manager", operator="eq", value=str(sys_id))],
        limit=1,
    )
    if managed:
        return "manager"
    groups = user_record.get("member_groups") or []
    if isinstance(groups, list) and groups:
        return "agent"
    return "end_user"


# Visibility matrix, by entity and role:
#
#   incident:
#     end_user      caller_id == self
#     agent         assigned_to == self  OR  assignment_group IN groups
#     manager       caller_id  IN {self, *reports}
#                   OR assigned_to IN {self, *reports}
#     admin         all
#
#   sys_user:
#     end_user      sys_id == self
#     agent / mgr   sys_id IN visible_user_sys_ids   (precomputed)
#     admin         all
#
#   sys_user_group:
#     end_user      none
#     agent         sys_id IN group_sys_ids
#     manager       sys_id IN managed_group_sys_ids
#     admin         all
#
#   kb_knowledge, category, _aggregate, _write_proposal:
#     all roles see them.


@dataclass(frozen=True)
class ViewScope:
    role: Role
    user_sys_id: str | None = None
    actor_name: str | None = None
    group_sys_ids: tuple[str, ...] = ()
    direct_report_sys_ids: tuple[str, ...] = ()
    managed_group_sys_ids: tuple[str, ...] = ()
    visible_user_sys_ids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def admin(cls) -> ViewScope:
        return cls(role="admin")

    @classmethod
    def from_session(cls, session: SessionState) -> ViewScope:
        return cls(
            role=session.role,
            user_sys_id=session.user_sys_id,
            actor_name=session.actor_name,
            group_sys_ids=tuple(session.group_sys_ids),
            direct_report_sys_ids=tuple(session.direct_report_sys_ids),
            managed_group_sys_ids=tuple(session.managed_group_sys_ids),
            visible_user_sys_ids=frozenset(session.visible_user_sys_ids),
        )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_view_pii(self) -> bool:
        return self.role == "admin"

    @property
    def can_write(self) -> bool:
        return True

    def can_aggregate(self) -> bool:
        return self.role != "end_user"

    def filter_records(
        self, entity_id: str, records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.is_admin:
            return records
        if entity_id in ("kb_knowledge", "category"):
            return records
        if entity_id.startswith("_"):
            return records
        predicate = self._predicate_for(entity_id)
        if predicate is None:
            # Unknown entity: deny by default.
            return []
        return [r for r in records if predicate(r)]

    def _predicate_for(
        self, entity_id: str,
    ) -> Callable[[dict[str, Any]], bool] | None:
        if entity_id == "incident":
            return self._incident_predicate
        if entity_id == "sys_user":
            return self._user_predicate
        if entity_id == "sys_user_group":
            return self._group_predicate
        return None

    def _incident_predicate(self, r: dict[str, Any]) -> bool:
        if self.role == "end_user":
            return bool(self.user_sys_id) and r.get("caller_id") == self.user_sys_id
        if self.role == "agent":
            if not self.user_sys_id:
                return False
            if r.get("assigned_to") == self.user_sys_id:
                return True
            ag = r.get("assignment_group")
            return ag is not None and ag in self.group_sys_ids
        if self.role == "manager":
            if not self.user_sys_id:
                return False
            visible = {self.user_sys_id, *self.direct_report_sys_ids}
            return (
                r.get("caller_id") in visible
                or r.get("assigned_to") in visible
            )
        return True

    def _user_predicate(self, r: dict[str, Any]) -> bool:
        sid = r.get("sys_id")
        if sid is None:
            return False
        if self.role == "end_user":
            return bool(self.user_sys_id) and sid == self.user_sys_id
        return sid in self.visible_user_sys_ids

    def _group_predicate(self, r: dict[str, Any]) -> bool:
        sid = r.get("sys_id")
        if sid is None:
            return False
        if self.role == "end_user":
            return False
        if self.role == "agent":
            return sid in self.group_sys_ids
        if self.role == "manager":
            return sid in self.managed_group_sys_ids
        return True

    def reject_find(
        self, entity_id: str, has_pk_or_name_filter: bool,
    ) -> str | None:
        """Only end_user is loud-rejected. Agents and managers have
        non-trivial visible sets; the row filter narrows automatically."""
        if self.is_admin or self.role != "end_user":
            return None
        if entity_id == "sys_user" and not has_pk_or_name_filter:
            return (
                "as end_user, you can only look up yourself "
                "(add a name filter or query just 'me')"
            )
        if entity_id == "sys_user_group":
            return "as end_user, group lookups are out of scope"
        return None

    def reject_aggregate(self) -> str | None:
        if self.can_aggregate():
            return None
        return f"as {self.role}, aggregate operations are out of scope"

    def context_for_planner_prompt(self) -> str:
        """Markdown block injected into the planner's prompt."""
        if self.is_admin:
            return (
                "## View scope\n\n"
                "You are acting as an **admin**. Full access to every table, "
                "every field (including those flagged sensitive), and every "
                "write action.\n"
            )

        actor = self.actor_name or self.user_sys_id or "anonymous"
        lines = [
            "## View scope",
            "",
            f"You are acting on behalf of **{actor}** "
            f"(`{self.user_sys_id}`, role: {self.role}).",
            "",
            "**What this actor can see:**",
        ]

        if self.role == "end_user":
            lines += [
                "- `incident`: only records where this actor is the caller",
                "- `sys_user`: only this actor's own record",
                "- `sys_user_group`: none",
                "- `kb_knowledge`, `category`: all (public)",
                "",
                "**Out of scope:**",
                "- listing other users (\"show me all users\")",
                "- aggregation queries (\"how many tickets are there\")",
                "- looking up other people's tickets",
                "",
                "When the user says \"my\" or \"me\", they mean "
                f"`{self.user_sys_id}`. When the request clearly exceeds "
                "the scope above, set `intent=out_of_scope` with a one-line "
                "clarification.",
            ]
        elif self.role == "agent":
            groups = ", ".join(self.group_sys_ids) or "(none)"
            lines += [
                "- `incident`: records assigned to this actor OR handled by "
                f"a group this actor is a member of (groups: {groups})",
                "- `sys_user`: this actor, this actor's manager, and "
                "fellow members of their groups",
                "- `sys_user_group`: only groups this actor is a member of",
                "- `kb_knowledge`, `category`: all (public)",
                "",
                "**How to scope:** emit normal `find` and `traverse` ops "
                "without inventing extra scope filters. The executor's row "
                "filter narrows results automatically.",
                "",
                "**Out of scope:**",
                "- queries explicitly targeting a team this actor isn't a "
                "member of",
                "",
                "When the user says \"my\" or \"me\", they mean "
                f"`{self.user_sys_id}`.",
            ]
        elif self.role == "manager":
            reports = ", ".join(self.direct_report_sys_ids) or "(none)"
            managed = ", ".join(self.managed_group_sys_ids) or "(none)"
            lines += [
                "- `incident`: records where the caller or assignee is "
                f"this actor or one of their direct reports ({reports})",
                "- `sys_user`: this actor, their manager, and their direct reports",
                f"- `sys_user_group`: groups this actor manages ({managed})",
                "- `kb_knowledge`, `category`: all (public)",
                "",
                "**How to scope:** emit normal `find` and `traverse` ops "
                "without inventing extra scope filters. The executor's row "
                "filter narrows results automatically.",
                "",
                "**Out of scope:**",
                "- queries about peers' teams",
                "- queries about reports-of-reports (only direct reports)",
                "",
                "\"My team\" means direct reports + groups this actor manages.",
            ]
        return "\n".join(lines) + "\n"
