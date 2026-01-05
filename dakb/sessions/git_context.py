"""
DAKB Session Management - Git Context Capture

Captures git repository state including current branch, commit, uncommitted
changes, and stash list for session persistence and handoff.

Version: 1.1
Created: 2025-12-08
Updated: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Changelog v1.1:
- ISS-073 Fix: Added argument validation to prevent command injection
- ISS-074 Fix: Added path traversal prevention
- ISS-076 Fix: Sanitized error messages to not expose sensitive paths

Features:
- Current branch and commit capture
- Uncommitted changes detection
- Staged vs unstaged changes tracking
- Stash list capture
- Remote tracking status
- Dirty working tree detection
- File change summary (added, modified, deleted counts)
"""

import logging
import os
import re
import subprocess
import time
from datetime import datetime
from typing import Optional, List, Tuple
from pathlib import Path

from .models import (
    GitContextSnapshot,
    GitFileChange,
    GitStashEntry,
    GitChangeType,
)

logger = logging.getLogger(__name__)


class GitContextCaptureError(Exception):
    """Error capturing git context."""
    pass


# ISS-073 Fix: Safe git argument pattern - only allow safe characters
SAFE_GIT_ARG_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\./@{}:=\s]+$')

# ISS-074 Fix: Allowed base paths for repository access
# Configure via DAKB_ALLOWED_PATHS environment variable (colon-separated)
def _get_allowed_base_paths() -> list:
    """Get allowed base paths from environment or use defaults."""
    import os
    custom = os.getenv("DAKB_ALLOWED_PATHS", "")
    if custom:
        return [Path(p) for p in custom.split(":") if p]
    return [
        Path.home() / "Documents",  # User's Documents folder
        Path.home() / "projects",   # Common projects folder
        Path("/tmp"),
        Path.home(),
    ]

ALLOWED_BASE_PATHS = _get_allowed_base_paths()


def _validate_git_arg(arg: str) -> bool:
    """
    Validate a git command argument is safe.

    ISS-073 Fix: Prevent command injection by validating arguments.

    Args:
        arg: Argument to validate

    Returns:
        True if safe, False otherwise
    """
    # Check for shell metacharacters and other dangerous patterns
    dangerous_patterns = [
        '&&', '||', ';', '|', '`', '$(',
        '$(', '${', '\n', '\r', '\x00'
    ]
    for pattern in dangerous_patterns:
        if pattern in arg:
            return False

    # Block dangerous git options that could execute commands
    dangerous_options = [
        '--exec', '--upload-pack', '--receive-pack',
        '-c core.sshCommand', '-c http.sslCAInfo',
        '--config core.sshCommand', '--config http.proxy'
    ]
    arg_lower = arg.lower()
    for opt in dangerous_options:
        if opt in arg_lower or arg_lower.startswith(opt.split()[0]):
            return False

    # Check length to prevent buffer overflow attempts
    if len(arg) > 1000:
        return False

    return True


def _validate_repository_path(path: Path) -> bool:
    """
    Validate repository path is within allowed directories.

    ISS-074 Fix: Prevent path traversal attacks.

    Args:
        path: Resolved path to validate

    Returns:
        True if path is allowed, False otherwise
    """
    # Reject paths containing traversal patterns before resolution
    path_str = str(path)
    if '..' in path_str:
        return False

    # Ensure path is resolved (no symlinks, no ..)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False

    # Verify the resolved path doesn't escape allowed directories
    # by checking that it's under one of the allowed bases
    for allowed_base in ALLOWED_BASE_PATHS:
        try:
            allowed_resolved = allowed_base.resolve()
            resolved.relative_to(allowed_resolved)
            return True
        except ValueError:
            continue

    return False


def _sanitize_error_message(message: str) -> str:
    """
    Sanitize error messages to not expose sensitive paths.

    ISS-076 Fix: Remove sensitive information from error messages.

    Args:
        message: Error message to sanitize

    Returns:
        Sanitized message
    """
    # Replace home directory path
    home = str(Path.home())
    if home in message:
        message = message.replace(home, "~")

    # Remove potential credentials from URLs
    cred_pattern = re.compile(r'://[^@]+@')
    message = cred_pattern.sub('://[REDACTED]@', message)

    return message


class GitContextCapture:
    """
    Captures complete git repository state.

    Provides methods to capture current branch, commit info, uncommitted
    changes, stash list, and remote tracking status.
    """

    def __init__(self, repository_path: str):
        """
        Initialize git context capture.

        Args:
            repository_path: Path to git repository root

        Raises:
            GitContextCaptureError: If path is not a valid git repository
                                   or path is not allowed (ISS-074)
        """
        self.repository_path = Path(repository_path).resolve()

        # ISS-074 Fix: Validate path is within allowed directories
        if not _validate_repository_path(self.repository_path):
            raise GitContextCaptureError(
                "Repository path not allowed - must be within approved directories"
            )

        if not self._is_git_repository():
            raise GitContextCaptureError(
                "Not a valid git repository"
            )

    def _is_git_repository(self) -> bool:
        """Check if path is a valid git repository."""
        git_dir = self.repository_path / ".git"
        return git_dir.exists() and git_dir.is_dir()

    def _run_git_command(
        self,
        args: List[str],
        timeout: int = 30,
        check: bool = True,
    ) -> Tuple[str, str, int]:
        """
        Run a git command and return output.

        Args:
            args: Git command arguments (without 'git')
            timeout: Command timeout in seconds
            check: Whether to raise on non-zero exit

        Returns:
            Tuple of (stdout, stderr, return_code)

        Raises:
            GitContextCaptureError: If command fails and check=True,
                                   or if arguments contain unsafe characters (ISS-073)
        """
        # ISS-073 Fix: Validate all arguments before execution
        for arg in args:
            if not _validate_git_arg(arg):
                raise GitContextCaptureError(
                    "Invalid git argument - contains unsafe characters"
                )

        cmd = ["git"] + args

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repository_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if check and result.returncode != 0:
                # ISS-076 Fix: Sanitize error messages
                sanitized_stderr = _sanitize_error_message(result.stderr)
                raise GitContextCaptureError(
                    f"Git command failed: {args[0] if args else 'unknown'}\n"
                    f"Error: {sanitized_stderr}"
                )

            return result.stdout.strip(), result.stderr.strip(), result.returncode

        except subprocess.TimeoutExpired:
            raise GitContextCaptureError(
                f"Git command timed out: {args[0] if args else 'unknown'}"
            )
        except FileNotFoundError:
            raise GitContextCaptureError("Git executable not found")

    def capture(self) -> GitContextSnapshot:
        """
        Capture complete git context.

        Returns:
            GitContextSnapshot with all repository state

        Raises:
            GitContextCaptureError: If capture fails
        """
        start_time = time.time()

        try:
            # Get repository info
            repo_name = self.repository_path.name
            remote_url = self._get_remote_url()

            # Get current state
            branch = self._get_current_branch()
            commit_hash = self._get_head_commit_hash()
            commit_message = self._get_head_commit_message()
            commit_author = self._get_head_commit_author()
            commit_date = self._get_head_commit_date()

            # Get remote tracking info
            tracking_branch = self._get_tracking_branch()
            ahead, behind = self._get_ahead_behind()

            # Get working tree state
            staged_changes = self._get_staged_changes()
            unstaged_changes = self._get_unstaged_changes()
            untracked_files = self._get_untracked_files()

            # Get stash list
            stash_list = self._get_stash_list()

            capture_time = (time.time() - start_time) * 1000  # Convert to ms

            return GitContextSnapshot(
                repository_name=repo_name,
                repository_path=str(self.repository_path),
                remote_url=remote_url,
                branch=branch,
                commit_hash=commit_hash,
                commit_message=commit_message,
                commit_author=commit_author,
                commit_date=commit_date,
                tracking_branch=tracking_branch,
                ahead_count=ahead,
                behind_count=behind,
                staged_changes=staged_changes,
                unstaged_changes=unstaged_changes,
                untracked_files=untracked_files,
                stash_list=stash_list,
                capture_duration_ms=capture_time,
            )

        except Exception as e:
            # ISS-076 Fix: Sanitize error messages before logging/raising
            sanitized_msg = _sanitize_error_message(str(e))
            logger.error(f"Failed to capture git context: {sanitized_msg}")
            raise GitContextCaptureError(f"Failed to capture git context: {sanitized_msg}")

    def _get_remote_url(self) -> Optional[str]:
        """Get remote origin URL."""
        try:
            stdout, _, _ = self._run_git_command(
                ["remote", "get-url", "origin"],
                check=False
            )
            return stdout if stdout else None
        except GitContextCaptureError:
            return None

    def _get_current_branch(self) -> str:
        """Get current branch name."""
        stdout, _, _ = self._run_git_command(
            ["rev-parse", "--abbrev-ref", "HEAD"]
        )
        return stdout

    def _get_head_commit_hash(self) -> str:
        """Get HEAD commit hash (full)."""
        stdout, _, _ = self._run_git_command(
            ["rev-parse", "HEAD"]
        )
        return stdout

    def _get_head_commit_message(self) -> Optional[str]:
        """Get HEAD commit message (first line)."""
        try:
            stdout, _, _ = self._run_git_command(
                ["log", "-1", "--format=%s"],
                check=False
            )
            return stdout if stdout else None
        except GitContextCaptureError:
            return None

    def _get_head_commit_author(self) -> Optional[str]:
        """Get HEAD commit author."""
        try:
            stdout, _, _ = self._run_git_command(
                ["log", "-1", "--format=%an <%ae>"],
                check=False
            )
            return stdout if stdout else None
        except GitContextCaptureError:
            return None

    def _get_head_commit_date(self) -> Optional[datetime]:
        """Get HEAD commit date."""
        try:
            stdout, _, _ = self._run_git_command(
                ["log", "-1", "--format=%ci"],
                check=False
            )
            if stdout:
                # Parse ISO format: "2025-12-08 12:00:00 -0500"
                # Remove timezone for simplicity
                date_str = stdout.rsplit(" ", 1)[0]
                return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except (GitContextCaptureError, ValueError):
            pass
        return None

    def _get_tracking_branch(self) -> Optional[str]:
        """Get upstream tracking branch."""
        try:
            stdout, _, returncode = self._run_git_command(
                ["rev-parse", "--abbrev-ref", "@{upstream}"],
                check=False
            )
            return stdout if returncode == 0 and stdout else None
        except GitContextCaptureError:
            return None

    def _get_ahead_behind(self) -> Tuple[int, int]:
        """Get commits ahead/behind remote."""
        try:
            stdout, _, returncode = self._run_git_command(
                ["rev-list", "--count", "--left-right", "@{upstream}...HEAD"],
                check=False
            )

            if returncode == 0 and stdout:
                parts = stdout.split()
                if len(parts) == 2:
                    return int(parts[1]), int(parts[0])  # ahead, behind
        except (GitContextCaptureError, ValueError):
            pass
        return 0, 0

    def _get_staged_changes(self) -> List[GitFileChange]:
        """Get list of staged changes."""
        try:
            stdout, _, _ = self._run_git_command(
                ["diff", "--cached", "--name-status"],
                check=False
            )

            changes = []
            for line in stdout.splitlines():
                if not line:
                    continue
                change = self._parse_status_line(line)
                if change:
                    changes.append(change)

            return changes
        except GitContextCaptureError:
            return []

    def _get_unstaged_changes(self) -> List[GitFileChange]:
        """Get list of unstaged changes."""
        try:
            stdout, _, _ = self._run_git_command(
                ["diff", "--name-status"],
                check=False
            )

            changes = []
            for line in stdout.splitlines():
                if not line:
                    continue
                change = self._parse_status_line(line)
                if change:
                    changes.append(change)

            return changes
        except GitContextCaptureError:
            return []

    def _get_untracked_files(self) -> List[str]:
        """Get list of untracked files."""
        try:
            stdout, _, _ = self._run_git_command(
                ["ls-files", "--others", "--exclude-standard"],
                check=False
            )

            return [f for f in stdout.splitlines() if f]
        except GitContextCaptureError:
            return []

    def _parse_status_line(self, line: str) -> Optional[GitFileChange]:
        """
        Parse a git status line.

        Args:
            line: Status line from git diff --name-status

        Returns:
            GitFileChange or None if cannot parse
        """
        parts = line.split("\t")
        if len(parts) < 2:
            return None

        status_char = parts[0][0]  # First character of status

        # Map status to change type
        status_map = {
            "A": GitChangeType.ADDED,
            "M": GitChangeType.MODIFIED,
            "D": GitChangeType.DELETED,
            "R": GitChangeType.RENAMED,
            "C": GitChangeType.COPIED,
        }

        change_type = status_map.get(status_char)
        if not change_type:
            return None

        file_path = parts[1]
        old_path = parts[2] if len(parts) > 2 else None

        # Get additions/deletions for this file
        additions, deletions = self._get_file_stat(file_path)

        return GitFileChange(
            file_path=file_path,
            change_type=change_type,
            old_path=old_path,
            additions=additions,
            deletions=deletions,
            is_binary=self._is_binary_file(file_path),
        )

    def _get_file_stat(self, file_path: str) -> Tuple[int, int]:
        """Get additions and deletions for a file."""
        try:
            stdout, _, _ = self._run_git_command(
                ["diff", "--numstat", "--", file_path],
                check=False
            )

            if stdout:
                parts = stdout.split()
                if len(parts) >= 2:
                    additions = int(parts[0]) if parts[0] != "-" else 0
                    deletions = int(parts[1]) if parts[1] != "-" else 0
                    return additions, deletions
        except (GitContextCaptureError, ValueError):
            pass
        return 0, 0

    def _is_binary_file(self, file_path: str) -> bool:
        """Check if a file is binary."""
        try:
            stdout, _, _ = self._run_git_command(
                ["diff", "--numstat", "--", file_path],
                check=False
            )
            # Binary files show as "-\t-\tfilename"
            return stdout.startswith("-\t-")
        except GitContextCaptureError:
            return False

    def _get_stash_list(self) -> List[GitStashEntry]:
        """Get list of stash entries."""
        try:
            stdout, _, _ = self._run_git_command(
                ["stash", "list", "--format=%gd|%gs|%H"],
                check=False
            )

            stashes = []
            for line in stdout.splitlines():
                if not line:
                    continue

                parts = line.split("|", 2)
                if len(parts) < 3:
                    continue

                # Parse stash reference: stash@{0}
                stash_ref = parts[0]
                try:
                    index = int(stash_ref.split("{")[1].rstrip("}"))
                except (IndexError, ValueError):
                    continue

                message = parts[1]
                commit_hash = parts[2]

                # Get branch from message (format: "WIP on branch: message")
                branch = "unknown"
                if " on " in message:
                    branch_part = message.split(" on ", 1)[1]
                    if ":" in branch_part:
                        branch = branch_part.split(":")[0]

                stashes.append(GitStashEntry(
                    stash_index=index,
                    message=message,
                    branch=branch,
                    commit_hash=commit_hash,
                ))

            return stashes
        except GitContextCaptureError:
            return []

    def get_diff_summary(self, max_size_kb: int = 100) -> Optional[str]:
        """
        Get human-readable diff summary.

        Args:
            max_size_kb: Maximum size in KB

        Returns:
            Diff summary string or None if too large
        """
        try:
            stdout, _, _ = self._run_git_command(
                ["diff", "--stat"],
                check=False
            )

            if len(stdout.encode('utf-8')) > max_size_kb * 1024:
                return f"[Diff too large: >{max_size_kb}KB]"

            return stdout if stdout else None
        except GitContextCaptureError:
            return None

    def get_full_diff(self, staged: bool = False) -> str:
        """
        Get full unified diff.

        Args:
            staged: Whether to get staged diff

        Returns:
            Full diff string
        """
        try:
            args = ["diff"]
            if staged:
                args.append("--cached")

            stdout, _, _ = self._run_git_command(args, check=False)
            return stdout
        except GitContextCaptureError:
            return ""

    def is_working_tree_clean(self) -> bool:
        """Check if working tree is clean (no uncommitted changes)."""
        try:
            stdout, _, _ = self._run_git_command(
                ["status", "--porcelain"],
                check=False
            )
            return len(stdout.strip()) == 0
        except GitContextCaptureError:
            return False

    def has_uncommitted_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        return not self.is_working_tree_clean()


def capture_git_context(repository_path: str) -> GitContextSnapshot:
    """
    Convenience function to capture git context.

    Args:
        repository_path: Path to git repository

    Returns:
        GitContextSnapshot with repository state

    Raises:
        GitContextCaptureError: If capture fails
    """
    capture = GitContextCapture(repository_path)
    return capture.capture()


def is_git_repository(path: str) -> bool:
    """
    Check if a path is a git repository.

    Args:
        path: Path to check

    Returns:
        True if path is a git repository
    """
    git_dir = Path(path) / ".git"
    return git_dir.exists() and git_dir.is_dir()


def find_repository_root(start_path: str) -> Optional[str]:
    """
    Find git repository root starting from a path.

    Args:
        start_path: Path to start searching from

    Returns:
        Repository root path or None if not in a repository
    """
    current = Path(start_path).resolve()

    while current != current.parent:
        if (current / ".git").exists():
            return str(current)
        current = current.parent

    # Check root
    if (current / ".git").exists():
        return str(current)

    return None
