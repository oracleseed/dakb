"""
DAKB Gateway Vault Routes

REST API routes for file vault operations: upload, download, list, delete, preflight.
All routes follow the Agentic API Design pattern with available_actions and instructions.

Endpoints:
- POST   /api/v1/vault/upload                            - Upload files
- GET    /api/v1/vault/preflight                         - Check upload feasibility
- GET    /api/v1/vault/{knowledge_id}                    - List files for entry
- GET    /api/v1/vault/{knowledge_id}/{file_id}/download - Download a file
- DELETE /api/v1/vault/{knowledge_id}/{file_id}          - Soft-delete a file
"""

import hashlib
import io
import json
import logging

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse

from ..agentic import ok_response, raise_issue
from ..middleware.auth import AccessChecker, check_rate_limit, get_current_agent

logger = logging.getLogger(__name__)


def create_vault_router(
    vault_repo,
    vault_backend,
    vault_settings,
    knowledge_repo=None,
) -> APIRouter:
    """Create vault router with injected dependencies.

    Args:
        vault_repo: VaultFileRepository instance
        vault_backend: FileVaultBackend instance
        vault_settings: VaultSettings instance
        knowledge_repo: KnowledgeRepository instance (for creating entries on new uploads)

    Returns:
        Configured APIRouter
    """
    # Router-level rate limiting, mirroring knowledge.py / moderation / aliases.
    # check_rate_limit depends on get_current_agent and is a no-op passthrough when
    # settings.rate_limit_enabled is False (default), so this does not change behavior
    # for the running gateway — it only aligns the dependency graph with the other routers.
    router = APIRouter(
        prefix="/api/v1/vault",
        tags=["vault"],
        dependencies=[Depends(check_rate_limit)],
    )

    # --- Access-control helper (mirrors knowledge.py) -----------------------------
    def _require_entry_access(agent, knowledge_id: str):
        """Enforce the parent knowledge entry's access policy on a vault operation.

        Resolves the parent knowledge entry and calls AccessChecker.require_access
        with the entry's access_level / allowed_agents / allowed_roles — exactly the
        pattern knowledge.py uses.

        Fail-open semantics (intentional, backward-compatible):
          * If knowledge_repo was not injected (None) — e.g. in unit fixtures that
            don't wire it — access control is skipped. Production ALWAYS injects it,
            so production ALWAYS enforces.
          * If the parent entry cannot be resolved (get_by_id -> None), we skip the
            access gate here; the per-route integrity check + 404 still apply. This
            preserves orphaned-file / legacy-id behavior and the existing 404 tests.

        Returns the resolved knowledge entry (or None) so callers can reuse it.
        """
        if knowledge_repo is None:
            return None
        try:
            knowledge = knowledge_repo.get_by_id(knowledge_id)
        except Exception:  # repo not reachable -> do not hard-fail the read path
            logger.warning("Vault access check: knowledge_repo.get_by_id failed for %s", knowledge_id)
            return None
        if not knowledge:
            return None
        AccessChecker.require_access(
            agent,
            knowledge.access_level,
            knowledge_id,
            allowed_agents=knowledge.allowed_agents,
            allowed_roles=knowledge.allowed_roles,
        )
        return knowledge

    # --- Upload (static route first) ---

    @router.post("/upload")
    async def upload_files(
        files: list[UploadFile] = File(..., description="Files to upload"),
        knowledge_id: str | None = Form(None, description="Existing entry to attach to"),
        metadata: str | None = Form(None, description="JSON metadata: title, summary, content, content_type, category, tags, file_descriptions"),
        hold_id: str | None = Form(None, description="Claim held files from a previous 422"),
        agent=Depends(get_current_agent),
    ):
        """Upload files to the DAKB vault. Follows Agentic API Design pattern."""
        from ...db.schemas import VaultFile, VaultFileStatus, generate_id

        # Parse metadata JSON
        meta = {}
        if metadata:
            try:
                meta = json.loads(metadata)
            except json.JSONDecodeError:
                raise_issue(
                    "VAULT.UPLOAD_FAILED",
                    status=400,
                    message="metadata field must be valid JSON — provide a JSON string with: title, summary, content, content_type, category, tags",
                    context={"field": "metadata"},
                )

        # Validate file count
        if len(files) > vault_settings.max_files_per_entry:
            raise_issue(
                "VAULT.LIMIT_EXCEEDED",
                status=422,
                message=f"Upload contains {len(files)} files — entry limit is {vault_settings.max_files_per_entry}. Reduce to {vault_settings.max_files_per_entry} files or split into multiple entries linked via related_knowledge_ids.",
                context={
                    "file_count": len(files),
                    "max_files_per_entry": vault_settings.max_files_per_entry,
                    "max_total_size_per_entry_mb": vault_settings.max_total_size_bytes // (1024 * 1024),
                },
            )

        # Check MIME types and read file data
        from ...vault.utils import is_executable_content
        file_data_list = []
        total_size = 0
        for upload_file in files:
            data = await upload_file.read()
            size = len(data)
            total_size += size
            mime = upload_file.content_type or "application/octet-stream"

            if mime not in vault_settings.allowed_mime_types:
                raise_issue(
                    "VAULT.TYPE_NOT_ALLOWED",
                    status=422,
                    message=f"File '{upload_file.filename}' has MIME type '{mime}' which is not allowed. Convert to one of the allowed types or remove it from the upload.",
                    context={
                        "filename": upload_file.filename,
                        "mime_type": mime,
                        "allowed_types": vault_settings.allowed_mime_types,
                    },
                )

            # Defense-in-depth: reject executable binaries regardless of declared MIME.
            # Vault stores files but never executes them — still, we don't accept
            # ELF/Mach-O/PE/Java class/shebang content to keep the store auditable.
            if is_executable_content(data):
                raise_issue(
                    "VAULT.EXECUTABLE_REJECTED",
                    status=422,
                    message=f"File '{upload_file.filename}' contains executable binary content (ELF/Mach-O/PE/Java/shell script) and cannot be stored. The vault accepts data files only.",
                    context={
                        "filename": upload_file.filename,
                        "declared_mime": mime,
                        "reason": "executable_content_detected",
                    },
                )

            checksum = hashlib.sha256(data).hexdigest()
            file_data_list.append({
                "data": data,
                "filename": upload_file.filename or "unnamed_file",
                "mime": mime,
                "size": size,
                "checksum": checksum,
            })

        # Check total size budget
        if knowledge_id:
            usage = vault_repo.get_budget_usage(knowledge_id)
            total_with_existing = usage["total_size_bytes"] + total_size
        else:
            total_with_existing = total_size

        if total_with_existing > vault_settings.max_total_size_bytes:
            raise_issue(
                "VAULT.SIZE_EXCEEDED",
                status=422,
                message=f"Total size {total_with_existing:,} bytes exceeds {vault_settings.max_total_size_bytes:,} byte budget. Reduce total file size or split into multiple entries.",
                context={
                    "total_size_bytes": total_with_existing,
                    "max_total_size_bytes": vault_settings.max_total_size_bytes,
                    "max_total_size_per_entry_mb": vault_settings.max_total_size_bytes // (1024 * 1024),
                    "file_breakdown": [{"name": f["filename"], "size_mb": round(f["size"] / 1024 / 1024, 2)} for f in file_data_list],
                },
            )

        # Generate knowledge_id if not provided — AND create the knowledge entry
        created_new_entry = False
        if not knowledge_id:
            if knowledge_repo and meta.get("title") and meta.get("content"):
                # Create a real knowledge entry from the metadata
                from ...db.schemas import Category, ContentType, KnowledgeCreate, KnowledgeSource
                try:
                    create_data = KnowledgeCreate(
                        title=meta["title"],
                        content=meta["content"],
                        content_type=ContentType(meta.get("content_type", "report")),
                        category=Category(meta.get("category", "general")),
                        tags=meta.get("tags", []),
                        confidence=0.8,
                    )
                    import socket
                    source = KnowledgeSource(
                        agent_id=agent.agent_id if hasattr(agent, "agent_id") else "unknown",
                        agent_type="claude",
                        machine_id=socket.gethostname(),
                    )
                    entry = knowledge_repo.create(create_data, source)
                    knowledge_id = entry.knowledge_id
                    created_new_entry = True
                    logger.info(f"Vault upload: created knowledge entry {knowledge_id}")
                except Exception as e:
                    logger.error(f"Vault upload: failed to create knowledge entry: {e}")
                    # Fallback: generate orphan ID (backward compat)
                    knowledge_id = generate_id("kn")
            else:
                # No knowledge_repo or missing metadata — generate ID only
                knowledge_id = generate_id("kn")
                if not knowledge_repo:
                    logger.warning("Vault upload: knowledge_repo not injected — knowledge entry NOT created")
                elif not meta.get("title") or not meta.get("content"):
                    logger.warning(f"Vault upload: missing title or content in metadata — knowledge entry NOT created (id={knowledge_id})")

        # Upload files to vault backend and create records
        created_files = []
        for fd in file_data_list:
            file_id = generate_id("vf")
            vault_path = f"vault/{knowledge_id}/{file_id}"

            # Upload to storage backend
            await vault_backend.upload(vault_path, io.BytesIO(fd["data"]), fd["mime"])

            # Create vault file record
            vault_file = VaultFile(
                file_id=file_id,
                knowledge_id=knowledge_id,
                filename=fd["filename"],
                mime_type=fd["mime"],
                size_bytes=fd["size"],
                checksum_sha256=fd["checksum"],
                vault_path=vault_path,
                description=None,
                status=VaultFileStatus.ACTIVE,
                uploaded_by=agent.agent_id if hasattr(agent, "agent_id") else "unknown",
            )

            # Apply file descriptions from metadata
            descriptions = meta.get("file_descriptions", [])
            for desc in descriptions:
                if desc.get("filename") == fd["filename"]:
                    vault_file.description = desc.get("description")
                    break

            vault_repo.create(vault_file)
            created_files.append({
                "file_id": file_id,
                "filename": fd["filename"],
                "size_bytes": fd["size"],
                "checksum_sha256": fd["checksum"],
                "status": "active",
            })

        # Update knowledge entry counters
        vault_repo.update_knowledge_counters(knowledge_id)

        return ok_response(
            data={
                "knowledge_id": knowledge_id,
                "vault_files": created_files,
            },
            status="created",
            actions=["vault_upload", "vault_download", "vault_list", "vault_delete", "get_knowledge"],
        )

    # --- Preflight ---

    @router.get("/preflight")
    async def preflight(
        mime: str = Query(..., description="MIME type to check"),
        size: int = Query(0, ge=0, description="File size in bytes"),
        knowledge_id: str | None = Query(None, description="Existing knowledge entry"),
        agent=Depends(get_current_agent),
    ):
        """Check if an upload would be accepted before sending file data."""
        mime_allowed = mime in vault_settings.allowed_mime_types

        result = {
            "mime_allowed": mime_allowed,
            "size_within_budget": size <= vault_settings.max_total_size_bytes,
            "max_file_size_bytes": vault_settings.max_total_size_bytes,
            "max_files_per_entry": vault_settings.max_files_per_entry,
            "allowed_mime_types": vault_settings.allowed_mime_types,
        }

        if knowledge_id:
            usage = vault_repo.get_budget_usage(knowledge_id)
            remaining_slots = vault_settings.max_files_per_entry - usage["file_count"]
            remaining_bytes = vault_settings.max_total_size_bytes - usage["total_size_bytes"]
            result["budget"] = {
                "files_used": usage["file_count"],
                "files_limit": vault_settings.max_files_per_entry,
                "size_used_bytes": usage["total_size_bytes"],
                "size_limit_bytes": vault_settings.max_total_size_bytes,
                "files_remaining": remaining_slots,
                "bytes_remaining": remaining_bytes,
            }

        suggestions = []
        if not mime_allowed:
            suggestions.append(
                f"MIME type '{mime}' is not allowed. "
                f"Supported types: {', '.join(vault_settings.allowed_mime_types)}"
            )

        return ok_response(
            data=result,
            actions=["vault_upload", "vault_preflight"],
            suggestions=suggestions,
        )

    # --- List files ---

    @router.get("/{knowledge_id}")
    async def list_files(
        knowledge_id: str,
        agent=Depends(get_current_agent),
    ):
        """List all active vault files for a knowledge entry."""
        # Enforce parent-entry access policy before listing (close IDOR).
        _require_entry_access(agent, knowledge_id)

        files = vault_repo.get_by_knowledge_id(knowledge_id)
        usage = vault_repo.get_budget_usage(knowledge_id)

        file_list = []
        for f in files:
            file_list.append({
                "file_id": f.file_id,
                "filename": f.filename,
                "mime_type": f.mime_type,
                "size_bytes": f.size_bytes,
                "description": f.description,
                "checksum_sha256": f.checksum_sha256,
                "status": f.status.value if hasattr(f.status, "value") else f.status,
                "download_url": f"/api/v1/vault/{knowledge_id}/{f.file_id}/download",
                "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
                "uploaded_by": f.uploaded_by,
            })

        return ok_response(
            data={
                "knowledge_id": knowledge_id,
                "files": file_list,
                "budget": {
                    "files_used": usage["file_count"],
                    "files_limit": vault_settings.max_files_per_entry,
                    "size_used_bytes": usage["total_size_bytes"],
                    "size_limit_bytes": vault_settings.max_total_size_bytes,
                },
            },
            actions=["vault_upload", "vault_download", "vault_delete", "vault_list", "get_knowledge"],
        )

    # --- Download ---

    @router.get("/{knowledge_id}/{file_id}/download")
    async def download_file(
        knowledge_id: str,
        file_id: str,
        agent=Depends(get_current_agent),
    ):
        """Download a vault file. Redirects to signed URL (S3) or streams (local)."""
        vault_file = vault_repo.get_by_id(file_id)
        if not vault_file:
            raise_issue(
                "VAULT.FILE_NOT_FOUND",
                status=404,
                message=f"File '{file_id}' not found in entry '{knowledge_id}'",
                context={"file_id": file_id, "knowledge_id": knowledge_id},
            )

        # Check file belongs to the knowledge entry
        if vault_file.knowledge_id != knowledge_id:
            raise_issue(
                "VAULT.FILE_NOT_FOUND",
                status=404,
                message=f"File '{file_id}' does not belong to entry '{knowledge_id}'",
                context={"file_id": file_id, "knowledge_id": knowledge_id},
            )

        # Enforce parent-entry access policy before serving bytes (close IDOR).
        _require_entry_access(agent, knowledge_id)

        # Try signed URL first (S3 backends)
        signed_url = await vault_backend.get_signed_url(vault_file.vault_path)
        if signed_url:
            return RedirectResponse(url=signed_url, status_code=307)

        # Stream file content (local backend)
        stream = await vault_backend.download(vault_file.vault_path)
        return StreamingResponse(
            stream,
            media_type=vault_file.mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{vault_file.filename}"',
                "Content-Length": str(vault_file.size_bytes),
            },
        )

    # --- Delete ---

    @router.delete("/{knowledge_id}/{file_id}")
    async def delete_file(
        knowledge_id: str,
        file_id: str,
        agent=Depends(get_current_agent),
    ):
        """Soft-delete a vault file. Sets status=deleted with 30-day purge TTL."""
        from ...db.schemas import AgentRole

        vault_file = vault_repo.get_by_id(file_id)
        if not vault_file:
            raise_issue(
                "VAULT.FILE_NOT_FOUND",
                status=404,
                message=f"File '{file_id}' not found",
                context={"file_id": file_id, "knowledge_id": knowledge_id},
            )

        # Verify file belongs to the knowledge entry in the URL
        if vault_file.knowledge_id != knowledge_id:
            raise_issue(
                "VAULT.FILE_NOT_FOUND",
                status=404,
                message=f"File '{file_id}' does not belong to entry '{knowledge_id}'",
                context={"file_id": file_id, "knowledge_id": knowledge_id},
            )

        # Enforce parent-entry access policy (close IDOR — RESTRICTED/SECRET).
        _require_entry_access(agent, knowledge_id)

        # Ownership gate: only the uploader or an ADMIN may delete the file.
        agent_id = getattr(agent, "agent_id", None)
        agent_role = getattr(agent, "role", None)
        is_owner = vault_file.uploaded_by == agent_id
        is_admin = agent_role == AgentRole.ADMIN
        if not (is_owner or is_admin):
            raise_issue(
                "VAULT.FILE_NOT_FOUND",
                status=403,
                message=(
                    f"Agent '{agent_id}' may not delete file '{file_id}' — only the "
                    f"uploader ('{vault_file.uploaded_by}') or an ADMIN agent can delete it."
                ),
                context={
                    "file_id": file_id,
                    "knowledge_id": knowledge_id,
                    "uploaded_by": vault_file.uploaded_by,
                    "requesting_agent": agent_id,
                },
            )

        vault_repo.soft_delete(file_id, ttl_days=vault_settings.soft_delete_ttl_days)
        vault_repo.update_knowledge_counters(vault_file.knowledge_id)

        return ok_response(
            data={
                "file_id": file_id,
                "knowledge_id": knowledge_id,
                "message": f"File '{vault_file.filename}' soft-deleted. Will be purged after {vault_settings.soft_delete_ttl_days} days.",
            },
            status="deleted",
            actions=["vault_list", "vault_upload", "get_knowledge"],
        )

    return router
