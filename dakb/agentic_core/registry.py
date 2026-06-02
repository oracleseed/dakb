"""Config-driven Issue and Action registries.

Loads issue codes and action definitions from YAML config files at startup.
Provides lookup, validation, and iteration for the agentic response system.
"""

import logging
from pathlib import Path

import yaml

from .envelope import AgenticAction, AgenticIssue

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent / "config"


class IssueDefinition:
    """A single issue loaded from issues.yaml."""

    def __init__(
        self,
        code: str,
        severity: str,
        message: str,
        category: str,
        retryable: bool = False,
        remediation_key: str | None = None,
        help_slug: str | None = None,
        deprecated: bool = False,
    ):
        self.code = code
        self.severity = severity
        self.message = message
        self.category = category
        self.retryable = retryable
        self.remediation_key = remediation_key
        self.help_slug = help_slug or code.lower().replace(".", "/")
        self.deprecated = deprecated

    def to_issue(
        self,
        field: str | None = None,
        message_override: str | None = None,
    ) -> AgenticIssue:
        return AgenticIssue(
            code=self.code,
            severity=self.severity,
            field=field,
            message=message_override or self.message,
            retryable=self.retryable,
            category=self.category,
            help=f"/api/help/errors/{self.code}",
        )


class IssueRegistry:
    """Loads issue definitions from YAML config."""

    def __init__(self, config_path: str | Path | None = None):
        self._issues: dict[str, IssueDefinition] = {}
        path = config_path or (CONFIG_DIR / "issues.yaml")
        self._load(path)

    def _load(self, config_path: str | Path):
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Issues config not found: {path}")
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for code, details in data.items():
            self._issues[code] = IssueDefinition(code=code, **details)
        logger.info(f"Loaded {len(self._issues)} issue definitions from {path.name}")

    def get(self, code: str) -> IssueDefinition | None:
        return self._issues.get(code)

    def all_codes(self) -> list[str]:
        return list(self._issues.keys())

    def by_category(self, category: str) -> list[IssueDefinition]:
        return [i for i in self._issues.values() if i.category == category]

    def all_definitions(self) -> list[IssueDefinition]:
        return list(self._issues.values())


class ActionDefinition:
    """A single action loaded from actions.yaml."""

    def __init__(
        self,
        name: str,
        method: str,
        endpoint: str,
        safe: bool,
        idempotent: bool,
        risk: str = "none",
        requires_confirmation: bool = False,
        parameters_schema: dict | None = None,
        description: str = "",
        help_slug: str | None = None,
    ):
        self.name = name
        self.method = method
        self.endpoint = endpoint
        self.safe = safe
        self.idempotent = idempotent
        self.risk = risk
        self.requires_confirmation = requires_confirmation
        self.parameters_schema = parameters_schema or {}
        self.description = description
        self.help_slug = help_slug or name

    def to_action(self) -> AgenticAction:
        return AgenticAction(
            name=self.name,
            method=self.method,
            endpoint=self.endpoint,
            safe=self.safe,
            idempotent=self.idempotent,
            risk=self.risk,
            requires_confirmation=self.requires_confirmation,
            parameters_schema=self.parameters_schema,
            help=f"/api/help/actions/{self.name}",
        )


class ActionRegistry:
    """Loads action definitions from YAML config."""

    def __init__(self, config_path: str | Path | None = None):
        self._actions: dict[str, ActionDefinition] = {}
        path = config_path or (CONFIG_DIR / "actions.yaml")
        self._load(path)

    def _load(self, config_path: str | Path):
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Actions config not found: {path}")
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for name, details in data.items():
            self._actions[name] = ActionDefinition(name=name, **details)
        logger.info(f"Loaded {len(self._actions)} action definitions from {path.name}")

    def get(self, name: str) -> ActionDefinition | None:
        return self._actions.get(name)

    def all_names(self) -> list[str]:
        return list(self._actions.keys())

    def get_actions_for(self, *names: str) -> list[AgenticAction]:
        """Get AgenticAction objects for the given action names."""
        actions = []
        for name in names:
            defn = self._actions.get(name)
            if defn:
                actions.append(defn.to_action())
        return actions

    def all_definitions(self) -> list[ActionDefinition]:
        return list(self._actions.values())


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_issue_registry: IssueRegistry | None = None
_action_registry: ActionRegistry | None = None


def get_issue_registry() -> IssueRegistry:
    global _issue_registry
    if _issue_registry is None:
        _issue_registry = IssueRegistry()
    return _issue_registry


def get_action_registry() -> ActionRegistry:
    global _action_registry
    if _action_registry is None:
        _action_registry = ActionRegistry()
    return _action_registry
