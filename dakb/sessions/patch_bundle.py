"""
DAKB Session Management - Patch Bundle

Creates and manages compressed patch bundles containing full git diffs
for cross-machine session transfer.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Generate unified diff for all uncommitted changes
- Include stash contents if requested
- Bundle compression (gzip + base64)
- Size validation (warn > 1MB, reject > 10MB)
- Patch metadata (timestamp, author, description)
- Dry-run application testing
"""

import base64
import gzip
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .git_context import GitContextCapture
from .models import (
    PatchBundle,
    decompress_content,
)

logger = logging.getLogger(__name__)


# Size limits
SIZE_WARNING_BYTES = 1024 * 1024  # 1MB
SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10MB


class PatchBundleError(Exception):
    """Error creating or applying patch bundle."""
    pass


class PatchBundleBuilder:
    """
    Builds patch bundles from git repository state.

    Creates compressed, transferable bundles containing all uncommitted
    changes that can be applied to another machine.
    """

    def __init__(self, repository_path: str):
        """
        Initialize patch bundle builder.

        Args:
            repository_path: Path to git repository

        Raises:
            PatchBundleError: If path is not a valid git repository
        """
        self.repository_path = Path(repository_path).resolve()
        self._git_capture = GitContextCapture(str(self.repository_path))

    def _run_git_command(
        self,
        args: list[str],
        timeout: int = 60,
        check: bool = True,
    ) -> tuple[str, str, int]:
        """
        Run a git command and return output.

        Args:
            args: Git command arguments
            timeout: Command timeout
            check: Raise on failure

        Returns:
            Tuple of (stdout, stderr, returncode)
        """
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
                raise PatchBundleError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"Error: {result.stderr}"
                )

            return result.stdout, result.stderr, result.returncode

        except subprocess.TimeoutExpired:
            raise PatchBundleError(
                f"Git command timed out: {' '.join(cmd)}"
            )
        except FileNotFoundError:
            raise PatchBundleError("Git executable not found")

    def create_bundle(
        self,
        session_id: str,
        agent_id: str,
        machine_id: str,
        include_stash: bool = False,
        description: str | None = None,
        compress: bool = True,
    ) -> PatchBundle:
        """
        Create a patch bundle from current repository state.

        Args:
            session_id: Session identifier
            agent_id: Agent creating the bundle
            machine_id: Machine creating the bundle
            include_stash: Whether to include stash contents
            description: Optional description
            compress: Whether to compress the bundle

        Returns:
            PatchBundle containing all changes

        Raises:
            PatchBundleError: If bundle creation fails
            ValueError: If patch size exceeds limit
        """
        # Get current git context
        git_context = self._git_capture.capture()

        # Generate patch content
        patch_content = self._generate_patch_content(
            include_staged=True,
            include_unstaged=True,
            include_untracked=True,
        )

        # Add stash content if requested
        stash_count = 0
        if include_stash and git_context.stash_list:
            stash_patches = self._get_stash_patches()
            if stash_patches:
                patch_content = (
                    f"# === STASH CONTENT ({len(stash_patches)} entries) ===\n\n"
                    + "\n\n".join(stash_patches)
                    + "\n\n# === END STASH ===\n\n"
                    + patch_content
                )
                stash_count = len(stash_patches)

        # Calculate original size
        original_bytes = patch_content.encode('utf-8')
        original_size = len(original_bytes)

        # Check size limit
        if original_size > SIZE_LIMIT_BYTES:
            raise ValueError(
                f"Patch size ({original_size} bytes) exceeds "
                f"10MB limit. Consider committing some changes."
            )

        # Compress if requested
        if compress:
            compressed = gzip.compress(original_bytes)
            encoded_content = base64.b64encode(compressed).decode('ascii')
            compressed_size = len(encoded_content)
        else:
            encoded_content = patch_content
            compressed_size = original_size

        # Calculate compression ratio
        compression_ratio = (
            compressed_size / original_size if original_size > 0 else 1.0
        )

        # Get changed files list
        files_changed = self._get_changed_files()

        # Get total additions/deletions
        additions, deletions = self._get_total_stats()

        # Generate warnings
        warnings = []
        if original_size > SIZE_WARNING_BYTES:
            warnings.append(
                f"Patch size ({original_size / 1024:.1f}KB) exceeds 1MB warning threshold"
            )

        # Test if patch can apply cleanly
        can_apply = self._test_patch_application(patch_content)

        return PatchBundle(
            source_session_id=session_id,
            source_agent_id=agent_id,
            source_machine_id=machine_id,
            base_branch=git_context.branch,
            base_commit=git_context.commit_hash,
            patch_content=encoded_content,
            patch_format="unified_diff",
            is_compressed=compress,
            original_size_bytes=original_size,
            compressed_size_bytes=compressed_size,
            compression_ratio=compression_ratio,
            files_count=len(files_changed),
            files_changed=files_changed,
            additions_total=additions,
            deletions_total=deletions,
            includes_stash=include_stash and stash_count > 0,
            stash_entries_count=stash_count,
            description=description,
            author=agent_id,
            is_valid=True,
            validation_warnings=warnings,
            can_apply_cleanly=can_apply,
        )

    def _generate_patch_content(
        self,
        include_staged: bool = True,
        include_unstaged: bool = True,
        include_untracked: bool = True,
    ) -> str:
        """
        Generate unified diff patch content.

        Args:
            include_staged: Include staged changes
            include_unstaged: Include unstaged changes
            include_untracked: Include untracked files

        Returns:
            Unified diff string
        """
        patches = []

        # Staged changes
        if include_staged:
            stdout, _, _ = self._run_git_command(
                ["diff", "--cached"],
                check=False
            )
            if stdout.strip():
                patches.append(f"# === STAGED CHANGES ===\n{stdout}")

        # Unstaged changes
        if include_unstaged:
            stdout, _, _ = self._run_git_command(
                ["diff"],
                check=False
            )
            if stdout.strip():
                patches.append(f"# === UNSTAGED CHANGES ===\n{stdout}")

        # Untracked files (as new file diffs)
        if include_untracked:
            untracked_patch = self._generate_untracked_patches()
            if untracked_patch:
                patches.append(f"# === UNTRACKED FILES ===\n{untracked_patch}")

        return "\n\n".join(patches)

    def _generate_untracked_patches(self) -> str:
        """Generate patch content for untracked files."""
        stdout, _, _ = self._run_git_command(
            ["ls-files", "--others", "--exclude-standard"],
            check=False
        )

        if not stdout.strip():
            return ""

        patches = []
        for file_path in stdout.strip().split("\n"):
            if not file_path:
                continue

            # Read file content
            full_path = self.repository_path / file_path
            if full_path.is_file():
                try:
                    with open(full_path, encoding='utf-8') as f:
                        content = f.read()

                    # Generate diff header for new file
                    lines = content.split("\n")
                    diff_lines = [
                        f"diff --git a/{file_path} b/{file_path}",
                        "new file mode 100644",
                        "--- /dev/null",
                        f"+++ b/{file_path}",
                        f"@@ -0,0 +1,{len(lines)} @@",
                    ]
                    diff_lines.extend([f"+{line}" for line in lines])
                    patches.append("\n".join(diff_lines))

                except (OSError, UnicodeDecodeError):
                    # Binary or unreadable file
                    patches.append(
                        f"# Binary file: {file_path}\n"
                        f"# Cannot include in text patch"
                    )

        return "\n\n".join(patches)

    def _get_stash_patches(self) -> list[str]:
        """Get patch content for all stash entries."""
        patches = []

        # Get stash list
        stdout, _, _ = self._run_git_command(
            ["stash", "list"],
            check=False
        )

        if not stdout.strip():
            return []

        for line in stdout.strip().split("\n"):
            if not line:
                continue

            # Extract stash reference
            if ":" not in line:
                continue

            stash_ref = line.split(":")[0]

            # Get stash diff
            try:
                diff_out, _, _ = self._run_git_command(
                    ["stash", "show", "-p", stash_ref],
                    check=False
                )

                if diff_out.strip():
                    patches.append(
                        f"# Stash: {line}\n"
                        f"# Reference: {stash_ref}\n"
                        f"{diff_out}"
                    )
            except PatchBundleError:
                pass

        return patches

    def _get_changed_files(self) -> list[str]:
        """Get list of all changed files."""
        files = set()

        # Staged files
        stdout, _, _ = self._run_git_command(
            ["diff", "--cached", "--name-only"],
            check=False
        )
        files.update(f for f in stdout.strip().split("\n") if f)

        # Unstaged files
        stdout, _, _ = self._run_git_command(
            ["diff", "--name-only"],
            check=False
        )
        files.update(f for f in stdout.strip().split("\n") if f)

        # Untracked files
        stdout, _, _ = self._run_git_command(
            ["ls-files", "--others", "--exclude-standard"],
            check=False
        )
        files.update(f for f in stdout.strip().split("\n") if f)

        return sorted(list(files))

    def _get_total_stats(self) -> tuple[int, int]:
        """Get total additions and deletions."""
        additions = 0
        deletions = 0

        # Staged
        stdout, _, _ = self._run_git_command(
            ["diff", "--cached", "--shortstat"],
            check=False
        )
        a, d = self._parse_shortstat(stdout)
        additions += a
        deletions += d

        # Unstaged
        stdout, _, _ = self._run_git_command(
            ["diff", "--shortstat"],
            check=False
        )
        a, d = self._parse_shortstat(stdout)
        additions += a
        deletions += d

        return additions, deletions

    def _parse_shortstat(self, stat_output: str) -> tuple[int, int]:
        """Parse shortstat output to get additions and deletions."""
        additions = 0
        deletions = 0

        if "insertion" in stat_output:
            parts = stat_output.split(",")
            for part in parts:
                if "insertion" in part:
                    try:
                        additions = int(part.strip().split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "deletion" in part:
                    try:
                        deletions = int(part.strip().split()[0])
                    except (ValueError, IndexError):
                        pass

        return additions, deletions

    def _test_patch_application(self, patch_content: str) -> bool | None:
        """
        Test if patch can be applied cleanly.

        Args:
            patch_content: Patch to test

        Returns:
            True if can apply cleanly, False if conflicts, None if error
        """
        try:
            # Create temp file with patch
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.patch',
                delete=False
            ) as f:
                f.write(patch_content)
                temp_path = f.name

            try:
                # Test apply with --check (dry run)
                _, _, returncode = self._run_git_command(
                    ["apply", "--check", temp_path],
                    check=False
                )
                return returncode == 0
            finally:
                os.unlink(temp_path)

        except Exception as e:
            logger.warning(f"Could not test patch application: {e}")
            return None


class PatchBundleApplier:
    """
    Applies patch bundles to a git repository.

    Handles decompression, validation, and application of patch bundles
    created by PatchBundleBuilder.
    """

    def __init__(self, repository_path: str):
        """
        Initialize patch bundle applier.

        Args:
            repository_path: Path to git repository
        """
        self.repository_path = Path(repository_path).resolve()

    def _run_git_command(
        self,
        args: list[str],
        timeout: int = 60,
        check: bool = True,
    ) -> tuple[str, str, int]:
        """Run a git command."""
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
                raise PatchBundleError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"Error: {result.stderr}"
                )

            return result.stdout, result.stderr, result.returncode

        except subprocess.TimeoutExpired:
            raise PatchBundleError(f"Git command timed out: {' '.join(cmd)}")
        except FileNotFoundError:
            raise PatchBundleError("Git executable not found")

    def validate_bundle(self, bundle: PatchBundle) -> tuple[bool, list[str]]:
        """
        Validate a patch bundle before application.

        Args:
            bundle: Patch bundle to validate

        Returns:
            Tuple of (is_valid, list of issues)
        """
        issues = []

        # Check if we're on the same base branch
        stdout, _, _ = self._run_git_command(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            check=False
        )
        current_branch = stdout.strip()

        if current_branch != bundle.base_branch:
            issues.append(
                f"Branch mismatch: patch is for '{bundle.base_branch}', "
                f"current branch is '{current_branch}'"
            )

        # Check if base commit exists
        _, _, returncode = self._run_git_command(
            ["cat-file", "-t", bundle.base_commit],
            check=False
        )
        if returncode != 0:
            issues.append(
                f"Base commit {bundle.base_commit[:8]} not found in repository"
            )

        # Test patch application
        patch_content = self._get_patch_content(bundle)
        can_apply = self._test_patch(patch_content)

        if can_apply is False:
            issues.append("Patch cannot be applied cleanly - conflicts detected")

        return len(issues) == 0, issues

    def apply_bundle(
        self,
        bundle: PatchBundle,
        dry_run: bool = False,
    ) -> tuple[bool, str]:
        """
        Apply a patch bundle.

        Args:
            bundle: Patch bundle to apply
            dry_run: If True, only test application

        Returns:
            Tuple of (success, message)
        """
        # Get decompressed patch content
        patch_content = self._get_patch_content(bundle)

        if dry_run:
            can_apply = self._test_patch(patch_content)
            if can_apply:
                return True, "Patch can be applied cleanly"
            else:
                return False, "Patch has conflicts"

        # Create temp file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.patch',
            delete=False
        ) as f:
            f.write(patch_content)
            temp_path = f.name

        try:
            # Apply patch
            _, stderr, returncode = self._run_git_command(
                ["apply", "--3way", temp_path],
                check=False
            )

            if returncode == 0:
                return True, f"Successfully applied patch with {bundle.files_count} files"
            else:
                return False, f"Failed to apply patch: {stderr}"

        finally:
            os.unlink(temp_path)

    def _get_patch_content(self, bundle: PatchBundle) -> str:
        """Get decompressed patch content from bundle."""
        if bundle.is_compressed:
            return decompress_content(bundle.patch_content)
        return bundle.patch_content

    def _test_patch(self, patch_content: str) -> bool:
        """Test if patch can be applied."""
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.patch',
            delete=False
        ) as f:
            f.write(patch_content)
            temp_path = f.name

        try:
            _, _, returncode = self._run_git_command(
                ["apply", "--check", temp_path],
                check=False
            )
            return returncode == 0
        finally:
            os.unlink(temp_path)

    def get_conflict_hints(self, bundle: PatchBundle) -> list[str]:
        """
        Get hints for resolving conflicts.

        Args:
            bundle: Patch bundle with potential conflicts

        Returns:
            List of hint strings
        """
        hints = []

        # Check if there are local changes
        stdout, _, _ = self._run_git_command(
            ["status", "--porcelain"],
            check=False
        )
        if stdout.strip():
            hints.append(
                "Working tree has uncommitted changes. "
                "Consider stashing or committing before applying."
            )

        # Check if we need to fetch
        _, _, returncode = self._run_git_command(
            ["cat-file", "-t", bundle.base_commit],
            check=False
        )
        if returncode != 0:
            hints.append(
                f"Base commit {bundle.base_commit[:8]} not found. "
                f"Try: git fetch origin"
            )

        # Check branch
        stdout, _, _ = self._run_git_command(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            check=False
        )
        current_branch = stdout.strip()

        if current_branch != bundle.base_branch:
            hints.append(
                f"Current branch '{current_branch}' differs from patch branch "
                f"'{bundle.base_branch}'. "
                f"Consider: git checkout {bundle.base_branch}"
            )

        return hints


def create_patch_bundle(
    repository_path: str,
    session_id: str,
    agent_id: str,
    machine_id: str,
    include_stash: bool = False,
    description: str | None = None,
) -> PatchBundle:
    """
    Convenience function to create a patch bundle.

    Args:
        repository_path: Path to git repository
        session_id: Session identifier
        agent_id: Agent creating bundle
        machine_id: Machine creating bundle
        include_stash: Whether to include stash
        description: Optional description

    Returns:
        PatchBundle with all changes
    """
    builder = PatchBundleBuilder(repository_path)
    return builder.create_bundle(
        session_id=session_id,
        agent_id=agent_id,
        machine_id=machine_id,
        include_stash=include_stash,
        description=description,
    )


def apply_patch_bundle(
    repository_path: str,
    bundle: PatchBundle,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Convenience function to apply a patch bundle.

    Args:
        repository_path: Path to git repository
        bundle: Patch bundle to apply
        dry_run: Only test application

    Returns:
        Tuple of (success, message)
    """
    applier = PatchBundleApplier(repository_path)
    return applier.apply_bundle(bundle, dry_run=dry_run)
