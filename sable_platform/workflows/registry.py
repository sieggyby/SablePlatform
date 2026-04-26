"""Workflow registry — register and look up named WorkflowDefinitions."""
from __future__ import annotations

from sable_platform.errors import SableError, WORKFLOW_NOT_FOUND
from sable_platform.workflows.models import WorkflowDefinition

_REGISTRY: dict[str, WorkflowDefinition] = {}


def register(definition: WorkflowDefinition) -> None:
    """Register a WorkflowDefinition under its name."""
    _REGISTRY[definition.name] = definition


def get(name: str) -> WorkflowDefinition:
    """Return a registered WorkflowDefinition by name. Raises SableError if not found."""
    if name not in _REGISTRY:
        raise SableError(
            WORKFLOW_NOT_FOUND,
            f"No workflow registered: '{name}'. Available: {list_all()}",
        )
    return _REGISTRY[name]


def list_all() -> list[str]:
    """Return all registered workflow names."""
    return sorted(_REGISTRY.keys())


def _auto_register() -> None:
    """Import builtin workflows to trigger their registration."""
    from sable_platform.workflows.builtins import prospect_diagnostic_sync  # noqa: F401
    from sable_platform.workflows.builtins import weekly_client_loop  # noqa: F401
    from sable_platform.workflows.builtins import alert_check  # noqa: F401
    from sable_platform.workflows.builtins import lead_discovery  # noqa: F401
    from sable_platform.workflows.builtins import onboard_client  # noqa: F401
    from sable_platform.workflows.builtins import client_checkin_loop  # noqa: F401


_auto_register()
