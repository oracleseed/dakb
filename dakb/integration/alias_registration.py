"""
DAKB Alias Registration Integration Module

Integrates the agent alias system with the self-registration flow.
When an agent self-registers, they can optionally provide an alias which
gets automatically registered to their token.

This enables the "Token Team with Aliases" pattern where multiple agents
from the same token can register different aliases for message routing.

Version: 1.0
Created: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Features:
- Register alias during agent registration (optional)
- Graceful handling of alias conflicts (registration continues)
- Support for multiple agents on same token with different aliases
- Backwards compatible with existing registration flow

Integration Points:
- AgentRepository (agent registration)
- AliasRepository (alias registration)
- Self-registration API (when fully implemented)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from ..db import (
    AgentRegister,
    AgentType,
    DakbAgent,
    DakbAgentAlias,
)
from ..db.collections import (
    AgentRepository,
    AliasRepository,
    get_dakb_client,
    get_dakb_repositories,
)

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class AliasConflictError(Exception):
    """
    Raised when an alias is already registered to another token.

    This error indicates the requested alias is unavailable but does not
    prevent agent registration from completing. The agent can still
    send/receive messages via their token_id.
    """

    def __init__(self, alias: str, message: str | None = None):
        self.alias = alias
        self.message = message or f"Alias '{alias}' is already registered to another token"
        super().__init__(self.message)


class AgentRegistrationError(Exception):
    """
    Raised when agent registration fails.

    Unlike AliasConflictError, this is a fatal error that prevents
    the agent from being registered.
    """

    def __init__(self, agent_id: str, message: str | None = None):
        self.agent_id = agent_id
        self.message = message or f"Failed to register agent '{agent_id}'"
        super().__init__(self.message)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class AgentRegistrationResult:
    """
    Result of an agent registration with optional alias.

    Provides detailed information about the registration outcome,
    including whether the alias was successfully registered.
    """
    # Agent registration result
    success: bool
    agent_id: str
    token_id: str
    agent: DakbAgent | None = None

    # Alias registration result (optional)
    alias_requested: str | None = None
    alias_registered: bool = False
    alias_record: DakbAgentAlias | None = None
    alias_conflict: bool = False
    alias_conflict_message: str | None = None

    # Metadata
    registration_timestamp: datetime = field(default_factory=datetime.utcnow)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "success": self.success,
            "agent_id": self.agent_id,
            "token_id": self.token_id,
            "registration_timestamp": self.registration_timestamp.isoformat(),
            "messages": self.messages,
        }

        if self.alias_requested:
            result["alias"] = {
                "requested": self.alias_requested,
                "registered": self.alias_registered,
                "conflict": self.alias_conflict,
            }
            if self.alias_conflict_message:
                result["alias"]["conflict_message"] = self.alias_conflict_message
            if self.alias_record:
                result["alias"]["record"] = {
                    "alias_id": self.alias_record.alias_id,
                    "alias": self.alias_record.alias,
                    "role": self.alias_record.role,
                    "is_active": self.alias_record.is_active,
                }

        return result


# =============================================================================
# CORE INTEGRATION FUNCTIONS
# =============================================================================

async def register_agent_with_alias(
    token_id: str,
    agent_name: str,
    agent_type: str = "claude",
    machine_id: str = "unknown",
    alias: str | None = None,
    role: str | None = None,
    alias_metadata: dict | None = None,
    capabilities: list[str] | None = None,
    specializations: list[str] | None = None,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> AgentRegistrationResult:
    """
    Register an agent and optionally create an alias.

    This function implements the integrated registration flow where:
    1. The agent is registered with standard agent registration
    2. If an alias is provided, attempt to register it
    3. If alias is already taken, log warning but don't fail registration
    4. Agent can still send/receive messages via token_id

    This supports the "Token Team with Aliases" concept where one token
    can register multiple aliases for different roles/personas.

    Args:
        token_id: Primary token identity for the agent (used for auth)
        agent_name: Display name for the agent
        agent_type: Type of AI agent (claude, gpt, gemini, grok, local)
        machine_id: Machine identifier for the agent
        alias: Optional alias to register (must be globally unique)
        role: Optional role for the alias (e.g., 'orchestration', 'code_review')
        alias_metadata: Optional metadata for the alias
        capabilities: Agent capabilities list
        specializations: Agent specialization list
        client: MongoDB client (optional, will use default if not provided)
        db_name: Database name

    Returns:
        AgentRegistrationResult with registration details and alias status

    Example:
        >>> result = await register_agent_with_alias(
        ...     token_id="my-agent",
        ...     agent_name="Backend Developer",
        ...     agent_type="claude",
        ...     alias="Backend",
        ...     role="implementation"
        ... )
        >>> if result.success:
        ...     print(f"Agent registered: {result.agent_id}")
        ...     if result.alias_registered:
        ...         print(f"Alias '{result.alias_requested}' registered successfully")
        ...     elif result.alias_conflict:
        ...         print(f"Alias conflict: {result.alias_conflict_message}")
    """
    # Get MongoDB client and repositories
    if client is None:
        client = get_dakb_client()

    repos = get_dakb_repositories(client, db_name)
    agent_repo: AgentRepository = repos["agents"]
    alias_repo: AliasRepository = repos["aliases"]

    # Initialize result
    result = AgentRegistrationResult(
        success=False,
        agent_id=token_id,
        token_id=token_id,
        alias_requested=alias,
    )

    # ==========================================================================
    # Step 1: Agent Registration
    # ==========================================================================

    try:
        # Parse agent type
        try:
            parsed_agent_type = AgentType(agent_type.lower())
        except ValueError:
            parsed_agent_type = AgentType.LOCAL
            result.messages.append(
                f"Unknown agent type '{agent_type}', defaulting to 'local'"
            )

        # Check if agent already exists
        existing_agent = agent_repo.get_by_id(token_id)

        if existing_agent:
            # Agent exists - update last seen and continue
            agent_repo.heartbeat(token_id, activity="re-registration")
            result.agent = existing_agent
            result.messages.append(f"Agent '{token_id}' already registered, updated heartbeat")
            logger.info(f"Agent '{token_id}' already exists, updated heartbeat")
        else:
            # Register new agent
            agent_data = AgentRegister(
                agent_id=token_id,
                display_name=agent_name,
                agent_type=parsed_agent_type,
                machine_id=machine_id,
                capabilities=capabilities or [],
                specializations=specializations or [],
            )

            registered_agent = agent_repo.register(agent_data)
            result.agent = registered_agent
            result.messages.append(f"Agent '{token_id}' registered successfully")
            logger.info(
                f"Agent registered: {token_id} (type: {agent_type}, "
                f"machine: {machine_id})"
            )

        result.success = True

    except DuplicateKeyError:
        # Race condition - agent was registered between check and insert
        # Try to fetch the existing agent
        existing_agent = agent_repo.get_by_id(token_id)
        if existing_agent:
            result.agent = existing_agent
            result.success = True
            result.messages.append(
                f"Agent '{token_id}' was registered by concurrent request, continuing"
            )
            logger.warning(
                f"Agent registration race condition for '{token_id}', "
                "using existing agent"
            )
        else:
            # This shouldn't happen, but handle it gracefully
            result.messages.append(f"Failed to register agent '{token_id}'")
            logger.error(
                f"Agent registration failed for '{token_id}': "
                "DuplicateKeyError but agent not found"
            )
            return result

    except Exception as e:
        result.messages.append(f"Agent registration failed: {str(e)}")
        logger.error(f"Agent registration error for '{token_id}': {e}", exc_info=True)
        return result

    # ==========================================================================
    # Step 2: Alias Registration (Optional)
    # ==========================================================================

    if alias and result.success:
        try:
            # Check if alias is available
            if not alias_repo.is_alias_available(alias):
                # Alias already taken
                result.alias_conflict = True
                result.alias_conflict_message = (
                    f"Alias '{alias}' is already registered to another token. "
                    f"Agent '{token_id}' can still send/receive messages via token_id."
                )
                result.messages.append(
                    f"Alias '{alias}' already taken, registration continues without alias"
                )
                logger.warning(
                    f"Alias '{alias}' requested by token '{token_id}' is already taken"
                )
            else:
                # Register the alias
                alias_record = alias_repo.register_alias(
                    token_id=token_id,
                    alias=alias,
                    role=role,
                    metadata=alias_metadata or {"agent_name": agent_name}
                )

                result.alias_registered = True
                result.alias_record = alias_record
                result.messages.append(f"Alias '{alias}' registered successfully")
                logger.info(
                    f"Alias '{alias}' registered to token '{token_id}' "
                    f"(role: {role or 'none'})"
                )

        except DuplicateKeyError:
            # Race condition - alias was registered between check and insert
            result.alias_conflict = True
            result.alias_conflict_message = (
                f"Alias '{alias}' was just registered by another token. "
                f"Agent '{token_id}' can still send/receive messages via token_id."
            )
            result.messages.append(
                f"Alias '{alias}' taken by concurrent request, "
                "registration continues without alias"
            )
            logger.warning(
                f"Alias registration race condition for '{alias}' "
                f"(requested by token '{token_id}')"
            )

        except Exception as e:
            # Other error - log but don't fail registration
            result.messages.append(f"Alias registration error: {str(e)}")
            logger.error(
                f"Alias registration error for '{alias}' (token '{token_id}'): {e}",
                exc_info=True
            )

    return result


def register_alias_for_existing_agent(
    token_id: str,
    alias: str,
    role: str | None = None,
    metadata: dict | None = None,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> DakbAgentAlias:
    """
    Register a new alias for an existing agent token.

    This is a synchronous convenience function for adding aliases
    to agents that are already registered. Unlike register_agent_with_alias,
    this raises AliasConflictError if the alias is taken.

    Args:
        token_id: Existing agent token identity
        alias: Alias to register (must be globally unique)
        role: Optional role for the alias
        metadata: Optional metadata for the alias
        client: MongoDB client (optional)
        db_name: Database name

    Returns:
        Registered alias record

    Raises:
        AliasConflictError: If alias is already taken
        ValueError: If token_id doesn't exist

    Example:
        >>> alias = register_alias_for_existing_agent(
        ...     token_id="my-agent",
        ...     alias="Reviewer",
        ...     role="code_review"
        ... )
    """
    if client is None:
        client = get_dakb_client()

    repos = get_dakb_repositories(client, db_name)
    agent_repo: AgentRepository = repos["agents"]
    alias_repo: AliasRepository = repos["aliases"]

    # Verify agent exists
    existing_agent = agent_repo.get_by_id(token_id)
    if not existing_agent:
        raise ValueError(f"Agent '{token_id}' is not registered")

    # Check alias availability
    if not alias_repo.is_alias_available(alias):
        raise AliasConflictError(alias)

    # Register alias
    try:
        return alias_repo.register_alias(
            token_id=token_id,
            alias=alias,
            role=role,
            metadata=metadata or {}
        )
    except DuplicateKeyError:
        raise AliasConflictError(
            alias,
            f"Alias '{alias}' was just registered by another token (race condition)"
        )


def get_aliases_for_agent(
    token_id: str,
    active_only: bool = True,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> list[DakbAgentAlias]:
    """
    Get all aliases registered to an agent token.

    Args:
        token_id: Agent token identity
        active_only: If True, only return active aliases
        client: MongoDB client (optional)
        db_name: Database name

    Returns:
        List of alias records for the token

    Example:
        >>> aliases = get_aliases_for_agent("my-agent")
        >>> for alias in aliases:
        ...     print(f"{alias.alias} ({alias.role})")
    """
    if client is None:
        client = get_dakb_client()

    repos = get_dakb_repositories(client, db_name)
    alias_repo: AliasRepository = repos["aliases"]

    return alias_repo.get_aliases_for_token(token_id, active_only=active_only)


def resolve_recipient_to_token(
    recipient: str,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> str | None:
    """
    Resolve a recipient (alias or token_id) to the underlying token_id.

    This is the primary function for message routing. When sending a message:
    1. First check if recipient is an alias
    2. If so, resolve to the owning token_id
    3. If not an alias, return as-is (assumed to be token_id)

    Args:
        recipient: Alias or token_id to resolve
        client: MongoDB client (optional)
        db_name: Database name

    Returns:
        Resolved token_id or None if alias not found and not a valid token

    Example:
        >>> token = resolve_recipient_to_token("TeamLead")
        >>> # Returns "my-agent" if TeamLead is registered to that token
    """
    if client is None:
        client = get_dakb_client()

    repos = get_dakb_repositories(client, db_name)
    alias_repo: AliasRepository = repos["aliases"]

    # Try to resolve as alias
    resolved = alias_repo.resolve_alias(recipient)

    if resolved:
        return resolved

    # Not an alias, return as-is (might be direct token_id)
    return recipient


def check_agent_has_alias(
    token_id: str,
    alias: str,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> bool:
    """
    Check if a specific alias belongs to an agent token.

    Useful for verifying ownership before operations.

    Args:
        token_id: Agent token identity
        alias: Alias to check
        client: MongoDB client (optional)
        db_name: Database name

    Returns:
        True if the alias belongs to the token, False otherwise
    """
    if client is None:
        client = get_dakb_client()

    repos = get_dakb_repositories(client, db_name)
    alias_repo: AliasRepository = repos["aliases"]

    alias_record = alias_repo.get_by_alias(alias)

    if alias_record and alias_record.token_id == token_id:
        return True

    return False


# =============================================================================
# INTEGRATION WITH SELF-REGISTRATION FLOW (STUBS)
# =============================================================================

async def handle_self_registration_with_alias(
    registration_request: dict,
    invite_token: str | None = None,
) -> AgentRegistrationResult:
    """
    Handle self-registration request with optional alias.

    This is a stub that will integrate with the full self-registration
    system when it is implemented (per DAKB_AGENT_SELF_REGISTRATION_PLAN.md).

    Current behavior:
    - Extracts alias from registration request
    - Calls register_agent_with_alias with the alias

    Future behavior (when self-registration is implemented):
    - Validate invite_token if provided
    - Create pending request if no invite_token
    - Auto-approve with invite_token
    - Register alias on approval

    Args:
        registration_request: Registration request data containing:
            - agent_id: Unique identifier for the agent
            - agent_type: Type of AI agent
            - purpose: Description of agent purpose
            - alias: Optional alias to register
            - role: Optional role for the alias
        invite_token: Optional invite token for auto-approval

    Returns:
        AgentRegistrationResult with registration outcome
    """
    # Extract fields from request
    agent_id = registration_request.get("agent_id", "")
    agent_type = registration_request.get("agent_type", "claude")
    agent_name = registration_request.get("display_name", agent_id)
    machine_id = registration_request.get("machine_id", "unknown")
    alias = registration_request.get("alias")
    role = registration_request.get("role")
    capabilities = registration_request.get("capabilities", [])
    specializations = registration_request.get("specializations", [])

    # For now, directly register the agent with alias
    # In the future, this will integrate with the approval queue
    result = await register_agent_with_alias(
        token_id=agent_id,
        agent_name=agent_name,
        agent_type=agent_type,
        machine_id=machine_id,
        alias=alias,
        role=role,
        capabilities=capabilities,
        specializations=specializations,
    )

    # Add note about invite token (future integration)
    if invite_token:
        result.messages.append(
            "Note: invite_token validation not yet implemented. "
            "Registration proceeded without invite validation."
        )

    return result


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_team_members_for_token(
    token_id: str,
    client: MongoClient | None = None,
    db_name: str = "dakb",
) -> dict:
    """
    Get all team members (aliases) for a token.

    Returns a structured view of the "Token Team" - all aliases
    registered to a single token, organized by role.

    Args:
        token_id: Agent token identity
        client: MongoDB client (optional)
        db_name: Database name

    Returns:
        Dictionary with team information:
        {
            "token_id": "my-agent",
            "team_size": 3,
            "members": [
                {"alias": "TeamLead", "role": "orchestration"},
                {"alias": "Reviewer", "role": "code_review"},
                {"alias": "Backend", "role": "implementation"}
            ],
            "by_role": {
                "orchestration": ["TeamLead"],
                "code_review": ["Reviewer"],
                "implementation": ["Backend"]
            }
        }
    """
    aliases = get_aliases_for_agent(token_id, active_only=True, client=client, db_name=db_name)

    members = []
    by_role: dict[str, list[str]] = {}

    for alias in aliases:
        members.append({
            "alias": alias.alias,
            "role": alias.role,
            "alias_id": alias.alias_id,
            "registered_at": alias.registered_at.isoformat(),
        })

        if alias.role:
            if alias.role not in by_role:
                by_role[alias.role] = []
            by_role[alias.role].append(alias.alias)

    return {
        "token_id": token_id,
        "team_size": len(members),
        "members": members,
        "by_role": by_role,
    }
