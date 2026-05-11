from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.core.data_store import DataStore
from src.core.schema_graph import SchemaGraph
from src.core.transformations import display_to_code
from src.write_path.approval_store import InMemoryApprovalStore
from src.write_path.proposal import WriteProposal


class WriteExecutionError(Exception):
    """Base error for write-path failures. The API layer maps subclasses to
    HTTP status codes."""


class ProposalMissing(WriteExecutionError):  # noqa: N818  -- spec-fixed name
    """Token unknown or already consumed -> 404."""


class ProposalExpired(WriteExecutionError):  # noqa: N818
    """TTL elapsed -> 410."""


class ProposalConflict(WriteExecutionError):  # noqa: N818
    """Optimistic-lock check failed -> 409."""


def confirm(
    *,
    token: str,
    confirm_flag: bool,
    store: DataStore,
    graph: SchemaGraph,
    approvals: InMemoryApprovalStore,
) -> tuple[str, dict[str, Any] | None]:
    """Apply a confirmed write or cancel a pending one.

    Returns (status, record) where status is one of
    "confirmed" | "cancelled". Raises one of the WriteExecutionError
    subclasses on failure.
    """
    proposal: WriteProposal | None = approvals.get(token)
    if proposal is None:
        raise ProposalMissing(f"proposal {token!r} not found")
    if datetime.now(UTC) > proposal.expires_at:
        approvals.delete(token)
        raise ProposalExpired(f"proposal {token!r} expired")

    if not confirm_flag:
        approvals.delete(token)
        return "cancelled", None

    if proposal.action == "update_incident":
        if not proposal.target_sys_id:
            raise WriteExecutionError("update proposal missing target_sys_id")
        current = store.get("incident", proposal.target_sys_id)
        if current is None:
            raise ProposalConflict("target no longer exists")
        if (
            proposal.current_values
            and current.get("sys_updated_on")
            != proposal.current_values.get("sys_updated_on")
        ):
            raise ProposalConflict(
                "target was modified since proposal; please re-issue"
            )
        coded = {
            k: display_to_code(v, f"incident.{k}", graph)
            for k, v in proposal.proposed_values.items()
        }
        record = store.update("incident", proposal.target_sys_id, coded)
    else:
        # create_incident
        coded = {
            k: display_to_code(v, f"incident.{k}", graph)
            for k, v in proposal.proposed_values.items()
        }
        record = store.create("incident", coded)

    approvals.delete(token)
    return "confirmed", record
