"""delegate_write — the single audited choke-point for CROSS-DOMAIN writes.

Table rule: an agent reads any table but writes only its OWN. When it needs a
write it doesn't own (e.g. a Chef moving a customer_order to PACKED), it routes
through here: permission is checked against a capability matrix, the OWNER
executor performs the write, and the action is audited. Same-domain writes call
their own executor directly and never touch this.

This is the second shared primitive (alongside send_and_await_reply). It is NOT
an LLM tool — tool code calls it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.customer import (
    execute_order_special_instructions_update,
    execute_order_status_transition,
)
from app.executors.driver import (
    execute_driver_trip_initialization,
    execute_driver_trip_phase_update,
)
from app.executors.master import execute_stop_status_update, execute_system_audit_log


class _Cap(NamedTuple):
    executor: Callable[..., Awaitable[Any]]   # the owner executor (single writer for the table)
    roles: frozenset[str]                     # roles permitted to request this write


# capability name -> (owner executor, allowed requesting roles)
CROSS_DOMAIN_WRITES: dict[str, _Cap] = {
    # customer_orders is owned by the Customer domain (DW1). Chef/Driver/Master
    # move an order through the pipeline (COOKING/PACKED/PICKED_UP/DELIVERED/...).
    "ORDER_STATUS": _Cap(execute_order_status_transition, frozenset({"MASTER", "CHEF", "DRIVER"})),
    # an accepted dietary note is written to the (customer-owned) order.
    "ORDER_NOTE": _Cap(execute_order_special_instructions_update, frozenset({"MASTER", "CHEF"})),
    # system_delivery_stops is system-owned; the Driver marks arrival/completion.
    "STOP_STATUS": _Cap(execute_stop_status_update, frozenset({"DRIVER", "MASTER"})),
    # driver_trip_status is Driver-owned; Master seeds the trip at cutoff, the Driver
    # advances its phase.
    "DRIVER_TRIP_INIT": _Cap(execute_driver_trip_initialization, frozenset({"MASTER"})),
    "DRIVER_TRIP_PHASE": _Cap(execute_driver_trip_phase_update, frozenset({"DRIVER", "MASTER"})),
}


async def delegate_write(
    session: AsyncSession, *, requesting_role: str, capability: str, **payload: Any
) -> dict[str, Any]:
    """Route a cross-domain write to its owner executor, gated + audited.

    Returns {status: WRITTEN|DENIED, result, message}. `payload` is passed straight
    to the owner executor as keyword args (the caller supplies the executor's args).
    """
    cap = CROSS_DOMAIN_WRITES.get(capability)
    if cap is None:
        return {"status": "DENIED", "result": None, "message": f"Unknown capability '{capability}'."}

    if requesting_role not in cap.roles:
        await execute_system_audit_log(
            session, event_type="DELEGATE_WRITE_DENIED", source_role=requesting_role,
            target_role="SYSTEM", order_id=payload.get("order_id"),
            payload={"capability": capability}, severity="WARN",
        )
        return {
            "status": "DENIED", "result": None,
            "message": f"Role {requesting_role} is not permitted to perform {capability}.",
        }

    result = await cap.executor(session, **payload)
    await execute_system_audit_log(
        session, event_type="DELEGATED_WRITE", source_role=requesting_role,
        order_id=payload.get("order_id"), payload={"capability": capability}, severity="INFO",
    )
    return {"status": "WRITTEN", "result": result, "message": f"{capability} written by {requesting_role}."}
