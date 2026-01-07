"""
DAKB Session Management - Session Handoff

Cross-machine session transfer protocol for seamless work continuation
between different agents and machines.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Export session with git context and patch bundle
- Import session with validation and patch application
- Conflict detection and resolution hints
- Session chain tracking (original -> transferred -> ...)
- Handoff package serialization
"""

import json
import logging

from .git_context import GitContextCapture, GitContextCaptureError, find_repository_root
from .models import (
    GitContextSnapshot,
    HandoffAccept,
    HandoffPackage,
    HandoffRequest,
    HandoffStatus,
    PatchBundle,
    Session,
    SessionCreate,
    SessionStatus,
    generate_handoff_id,
)
from .patch_bundle import (
    PatchBundleApplier,
    PatchBundleBuilder,
    PatchBundleError,
)
from .repository import HandoffRepository, SessionRepository

logger = logging.getLogger(__name__)


class HandoffError(Exception):
    """Error during session handoff."""
    pass


class SessionHandoffManager:
    """
    Manages session handoff operations.

    Provides methods to export sessions for transfer, import received
    sessions, and manage the handoff lifecycle.
    """

    def __init__(
        self,
        session_repo: SessionRepository,
        handoff_repo: HandoffRepository,
    ):
        """
        Initialize handoff manager.

        Args:
            session_repo: Session repository for CRUD operations
            handoff_repo: Handoff repository for tracking transfers
        """
        self.session_repo = session_repo
        self.handoff_repo = handoff_repo

    # =========================================================================
    # EXPORT OPERATIONS
    # =========================================================================

    def export_session(
        self,
        session_id: str,
        target_agent_id: str | None = None,
        target_machine_id: str | None = None,
        include_git_context: bool = True,
        include_patch_bundle: bool = True,
        include_stash: bool = False,
        reason: str | None = None,
        notes: str | None = None,
    ) -> HandoffPackage:
        """
        Export a session for handoff.

        Creates a complete handoff package containing the session state,
        git context, and patch bundle for transfer to another machine.

        Args:
            session_id: Session to export
            target_agent_id: Target agent (None = any)
            target_machine_id: Target machine (None = any)
            include_git_context: Include git state snapshot
            include_patch_bundle: Include uncommitted changes
            include_stash: Include git stash in patch
            reason: Reason for handoff
            notes: Notes for the receiving agent

        Returns:
            HandoffPackage ready for transfer

        Raises:
            HandoffError: If export fails
        """
        # Get session
        session = self.session_repo.get_by_id(session_id)
        if not session:
            raise HandoffError(f"Session not found: {session_id}")

        # Check session can be handed off
        if session.status in [SessionStatus.COMPLETED, SessionStatus.ABANDONED]:
            raise HandoffError(
                f"Cannot handoff session with status: {session.status.value}"
            )

        handoff_id = generate_handoff_id()

        # Capture git context if requested and working directory is set
        git_context = None
        if include_git_context and session.metadata.working_directory:
            try:
                repo_path = find_repository_root(session.metadata.working_directory)
                if repo_path:
                    capture = GitContextCapture(repo_path)
                    git_context = capture.capture()
                    # Save to session
                    self.session_repo.save_git_context(session_id, git_context)
            except GitContextCaptureError as e:
                logger.warning(f"Could not capture git context: {e}")

        # Create patch bundle if requested
        patch_bundle = None
        if include_patch_bundle and session.metadata.working_directory:
            try:
                repo_path = find_repository_root(session.metadata.working_directory)
                if repo_path:
                    builder = PatchBundleBuilder(repo_path)
                    patch_bundle = builder.create_bundle(
                        session_id=session_id,
                        agent_id=session.agent_id,
                        machine_id=session.machine_id,
                        include_stash=include_stash,
                        description=notes,
                    )
                    # Save to session
                    self.session_repo.save_patch_bundle(session_id, patch_bundle)
            except (PatchBundleError, ValueError) as e:
                logger.warning(f"Could not create patch bundle: {e}")

        # Generate application instructions
        apply_instructions = self._generate_apply_instructions(
            session,
            git_context,
            patch_bundle,
        )

        # Generate conflict hints
        conflict_hints = self._generate_conflict_hints(
            git_context,
            patch_bundle,
        )

        # Calculate package size
        package_size = self._calculate_package_size(
            session,
            git_context,
            patch_bundle,
        )

        # Create handoff request
        handoff_request = HandoffRequest(
            handoff_id=handoff_id,
            source_session_id=session_id,
            source_agent_id=session.agent_id,
            source_machine_id=session.machine_id,
            target_agent_id=target_agent_id,
            target_machine_id=target_machine_id,
            include_git_context=include_git_context and git_context is not None,
            include_patch_bundle=include_patch_bundle and patch_bundle is not None,
            include_stash=include_stash,
            reason=reason,
            notes=notes,
            status=HandoffStatus.PENDING,
        )

        # Store handoff request
        self.handoff_repo.create_handoff_request(handoff_request)

        # Create package
        package = HandoffPackage(
            handoff_id=handoff_id,
            session=session,
            git_context=git_context,
            patch_bundle=patch_bundle,
            apply_instructions=apply_instructions,
            conflict_hints=conflict_hints,
            package_size_bytes=package_size,
            is_valid=True,
            created_by=session.agent_id,
        )

        logger.info(
            f"Exported session {session_id} for handoff "
            f"(id: {handoff_id}, size: {package_size} bytes)"
        )

        return package

    def _generate_apply_instructions(
        self,
        session: Session,
        git_context: GitContextSnapshot | None,
        patch_bundle: PatchBundle | None,
    ) -> list[str]:
        """Generate step-by-step instructions for applying handoff."""
        instructions = []

        instructions.append(
            f"1. This session was created by agent '{session.agent_id}' "
            f"on machine '{session.machine_id}'"
        )

        if session.metadata.task_description:
            instructions.append(
                f"2. Task: {session.metadata.task_description}"
            )

        if git_context:
            instructions.append(
                f"3. Checkout branch: git checkout {git_context.branch}"
            )

            if git_context.commit_hash:
                instructions.append(
                    f"4. Base commit: {git_context.commit_hash[:12]} "
                    f"({git_context.commit_message or 'No message'})"
                )

        if patch_bundle:
            instructions.append(
                f"5. Apply patch bundle with {patch_bundle.files_count} files "
                f"(+{patch_bundle.additions_total}/-{patch_bundle.deletions_total})"
            )

        if session.metadata.working_files:
            files_str = ", ".join(session.metadata.working_files[:5])
            if len(session.metadata.working_files) > 5:
                files_str += f" (+{len(session.metadata.working_files) - 5} more)"
            instructions.append(f"6. Working files: {files_str}")

        if session.metadata.current_step:
            instructions.append(
                f"7. Current step: {session.metadata.current_step}"
            )

        return instructions

    def _generate_conflict_hints(
        self,
        git_context: GitContextSnapshot | None,
        patch_bundle: PatchBundle | None,
    ) -> list[str]:
        """Generate hints for potential conflicts."""
        hints = []

        if git_context and not git_context.is_clean:
            hints.append(
                "Source session had uncommitted changes. "
                "Ensure clean working tree before applying."
            )

        if git_context and git_context.behind_count > 0:
            hints.append(
                f"Branch was {git_context.behind_count} commits behind remote. "
                "Consider pulling latest changes."
            )

        if patch_bundle and patch_bundle.is_size_warning():
            hints.append(
                f"Large patch bundle ({patch_bundle.original_size_bytes / 1024:.1f}KB). "
                "Review changes carefully before applying."
            )

        if patch_bundle and not patch_bundle.can_apply_cleanly:
            hints.append(
                "Patch may have conflicts. Manual resolution may be required."
            )

        return hints

    def _calculate_package_size(
        self,
        session: Session,
        git_context: GitContextSnapshot | None,
        patch_bundle: PatchBundle | None,
    ) -> int:
        """Calculate total package size in bytes."""
        size = len(session.model_dump_json().encode('utf-8'))

        if git_context:
            size += len(git_context.model_dump_json().encode('utf-8'))

        if patch_bundle:
            size += patch_bundle.compressed_size_bytes

        return size

    # =========================================================================
    # IMPORT OPERATIONS
    # =========================================================================

    def import_session(
        self,
        package: HandoffPackage,
        agent_id: str,
        machine_id: str,
        apply_patch: bool = True,
        target_directory: str | None = None,
    ) -> Session:
        """
        Import a session from a handoff package.

        Creates a new session continuing from the handoff, optionally
        applying the patch bundle.

        Args:
            package: Handoff package to import
            agent_id: Agent accepting the handoff
            machine_id: Machine accepting the handoff
            apply_patch: Whether to apply the patch bundle
            target_directory: Override working directory

        Returns:
            New session created from handoff

        Raises:
            HandoffError: If import fails
        """
        # Validate package
        if not package.is_valid:
            raise HandoffError(
                f"Invalid handoff package: {', '.join(package.validation_errors)}"
            )

        source_session = package.session

        # Determine working directory
        working_dir = target_directory or source_session.metadata.working_directory

        # Apply patch if requested
        patch_applied = False
        patch_message = ""

        if apply_patch and package.patch_bundle and working_dir:
            repo_path = find_repository_root(working_dir)
            if repo_path:
                try:
                    applier = PatchBundleApplier(repo_path)

                    # Validate first
                    is_valid, issues = applier.validate_bundle(package.patch_bundle)
                    if not is_valid:
                        logger.warning(f"Patch validation issues: {issues}")

                    # Apply patch
                    success, message = applier.apply_bundle(package.patch_bundle)
                    patch_applied = success
                    patch_message = message

                    if not success:
                        logger.warning(f"Patch application failed: {message}")

                except PatchBundleError as e:
                    logger.error(f"Error applying patch: {e}")
                    patch_message = str(e)

        # Create new session
        create_data = SessionCreate(
            agent_id=agent_id,
            machine_id=machine_id,
            agent_type=source_session.agent_type,
            working_directory=working_dir,
            task_description=source_session.metadata.task_description,
            timeout_minutes=source_session.timeout_minutes,
            loaded_contexts=source_session.metadata.loaded_contexts,
            working_files=source_session.metadata.working_files,
            parent_session_id=source_session.session_id,
        )

        new_session = self.session_repo.create_session(create_data)

        # Update handoff status
        result_status = HandoffStatus.APPLIED if patch_applied else HandoffStatus.ACCEPTED
        self.handoff_repo.update_handoff_status(
            handoff_id=package.handoff_id,
            status=result_status,
            result_session_id=new_session.session_id,
        )

        # Mark source session as handed off
        self.session_repo.mark_handed_off(
            session_id=source_session.session_id,
            target_agent_id=agent_id,
            target_machine_id=machine_id,
            notes=f"Handoff successful. New session: {new_session.session_id}. "
                  f"Patch: {patch_message}",
        )

        logger.info(
            f"Imported session {source_session.session_id} -> "
            f"{new_session.session_id} "
            f"(patch applied: {patch_applied})"
        )

        return new_session

    def accept_handoff(
        self,
        accept: HandoffAccept,
    ) -> tuple[bool, str, Session | None]:
        """
        Accept a pending handoff request.

        Args:
            accept: Acceptance parameters

        Returns:
            Tuple of (success, message, new session or None)
        """
        # Get handoff request
        request = self.handoff_repo.get_handoff_request(accept.handoff_id)
        if not request:
            return False, f"Handoff not found: {accept.handoff_id}", None

        if request.status != HandoffStatus.PENDING:
            return False, f"Handoff already processed: {request.status.value}", None

        # Get source session
        session = self.session_repo.get_by_id(request.source_session_id)
        if not session:
            return False, f"Source session not found: {request.source_session_id}", None

        # Create handoff package
        try:
            package = self.export_session(
                session_id=request.source_session_id,
                include_git_context=request.include_git_context,
                include_patch_bundle=request.include_patch_bundle,
                include_stash=request.include_stash,
            )
            package.handoff_id = accept.handoff_id

            # Import session
            new_session = self.import_session(
                package=package,
                agent_id=accept.agent_id,
                machine_id=accept.machine_id,
                apply_patch=accept.apply_patch,
                target_directory=accept.target_directory,
            )

            return True, "Handoff accepted successfully", new_session

        except HandoffError as e:
            # Update status to failed
            self.handoff_repo.update_handoff_status(
                handoff_id=accept.handoff_id,
                status=HandoffStatus.FAILED,
                error_message=str(e),
            )
            return False, str(e), None

    def reject_handoff(
        self,
        handoff_id: str,
        reason: str | None = None,
    ) -> bool:
        """
        Reject a pending handoff.

        Args:
            handoff_id: Handoff to reject
            reason: Optional rejection reason

        Returns:
            True if rejection was recorded
        """
        result = self.handoff_repo.update_handoff_status(
            handoff_id=handoff_id,
            status=HandoffStatus.REJECTED,
            error_message=reason,
        )
        return result is not None

    def cancel_handoff(
        self,
        handoff_id: str,
        agent_id: str,
    ) -> bool:
        """
        Cancel a pending handoff (source agent only).

        Args:
            handoff_id: Handoff to cancel
            agent_id: Agent requesting cancellation

        Returns:
            True if cancellation was recorded
        """
        request = self.handoff_repo.get_handoff_request(handoff_id)
        if not request:
            return False

        # Only source agent can cancel
        if request.source_agent_id != agent_id:
            return False

        result = self.handoff_repo.update_handoff_status(
            handoff_id=handoff_id,
            status=HandoffStatus.CANCELLED,
        )
        return result is not None

    # =========================================================================
    # QUERY OPERATIONS
    # =========================================================================

    def get_pending_handoffs_for_agent(
        self,
        agent_id: str,
        machine_id: str | None = None,
    ) -> list[HandoffRequest]:
        """
        Get pending handoffs for an agent.

        Args:
            agent_id: Agent to check for
            machine_id: Optional machine filter

        Returns:
            List of pending handoff requests
        """
        return self.handoff_repo.get_pending_handoffs(
            target_agent_id=agent_id,
            target_machine_id=machine_id,
        )

    def get_session_chain(self, session_id: str) -> list[Session]:
        """
        Get the full session chain.

        Args:
            session_id: Any session in the chain

        Returns:
            List of sessions from original to current
        """
        return self.session_repo.get_session_chain(session_id)


# =============================================================================
# SERIALIZATION HELPERS
# =============================================================================

def serialize_handoff_package(package: HandoffPackage) -> str:
    """
    Serialize a handoff package to JSON.

    Args:
        package: Package to serialize

    Returns:
        JSON string
    """
    return package.model_dump_json(indent=2)


def deserialize_handoff_package(json_str: str) -> HandoffPackage:
    """
    Deserialize a handoff package from JSON.

    Args:
        json_str: JSON string

    Returns:
        HandoffPackage

    Raises:
        ValueError: If deserialization fails
    """
    data = json.loads(json_str)
    return HandoffPackage(**data)


def export_package_to_file(package: HandoffPackage, file_path: str) -> None:
    """
    Export a handoff package to a file.

    Args:
        package: Package to export
        file_path: Destination file path
    """
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(serialize_handoff_package(package))


def import_package_from_file(file_path: str) -> HandoffPackage:
    """
    Import a handoff package from a file.

    Args:
        file_path: Source file path

    Returns:
        HandoffPackage

    Raises:
        FileNotFoundError: If file not found
        ValueError: If deserialization fails
    """
    with open(file_path, encoding='utf-8') as f:
        return deserialize_handoff_package(f.read())
