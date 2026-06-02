"""
DAKB MCP Vault Tool Handlers

Handler implementations for vault upload and download MCP tools.
These handlers call the DAKB Gateway vault REST API endpoints.

Upload uses multipart/form-data (file binaries + metadata JSON).
Download returns file info and download URL.
"""

import json
import logging
import os
from typing import Any

from .handlers import ToolResponse, get_client

logger = logging.getLogger(__name__)


def _unwrap_envelope(result: dict) -> dict:
    """Unwrap agentic envelope if present."""
    if isinstance(result, dict) and "data" in result and "meta" in result:
        return result["data"] if result["data"] is not None else result
    return result


async def handle_vault_upload(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_vault_upload tool call.

    Reads files from local disk, uploads as multipart/form-data
    to the vault REST endpoint.

    Args:
        args: Tool arguments:
            - files (required): List of local file paths
            - knowledge_id: Attach to existing entry
            - title, summary, content, content_type, category, tags: New entry metadata
            - file_descriptions: Per-file descriptions
    """
    file_paths = args.get("files")
    if not file_paths:
        return ToolResponse(
            success=False,
            error="Missing required parameter: files (list of local file paths)",
            error_code="missing_param",
        )

    # Validate files exist
    for fp in file_paths:
        if not os.path.exists(fp):
            return ToolResponse(
                success=False,
                error=f"File not found: {fp}",
                error_code="file_not_found",
            )

    try:
        client = await get_client()

        # Build metadata JSON (everything except files goes here)
        metadata = {}
        for key in ["knowledge_id", "title", "summary", "content",
                    "content_type", "category", "tags", "file_descriptions"]:
            if key in args and args[key] is not None:
                metadata[key] = args[key]

        # Build multipart form data
        # The REST endpoint expects: files=<binary>, metadata=<json string>
        http_client = await client._get_http_client()
        headers = client._get_headers()
        # Remove Content-Type — httpx sets it for multipart automatically
        headers.pop("Content-Type", None)

        files_data = []
        for fp in file_paths:
            filename = os.path.basename(fp)
            # Handle stays open across the multipart POST; closed in the
            # cleanup loop below after the request completes (intentional).
            files_data.append(("files", (filename, open(fp, "rb"))))  # noqa: SIM115

        form_data = {}
        if metadata:
            form_data["metadata"] = json.dumps(metadata)
        if metadata.get("knowledge_id"):
            form_data["knowledge_id"] = metadata["knowledge_id"]

        url = f"{client.config.gateway_url}/api/v1/vault/upload"
        response = await http_client.post(
            url,
            headers=headers,
            files=files_data,
            data=form_data,
            timeout=60.0,
        )

        # Close file handles
        for _, (_, fh) in files_data:
            fh.close()

        if response.status_code >= 400:
            error_body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            error_msg = error_body.get("detail", error_body.get("error", f"HTTP {response.status_code}"))
            # Unwrap agentic envelope error if present
            if "issues" in error_body and error_body["issues"]:
                error_msg = error_body["issues"][0].get("message", error_msg)
            return ToolResponse(
                success=False,
                error=f"Upload failed: {error_msg}",
                error_code="vault_upload_error",
            )

        result = response.json()
        result = _unwrap_envelope(result)

        return ToolResponse(
            success=True,
            data=result,
        )

    except Exception as e:
        logger.error(f"Vault upload failed: {e}")
        return ToolResponse(
            success=False,
            error=f"Vault upload failed: {str(e)}",
            error_code="vault_upload_error",
        )


async def handle_vault_download(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_vault_download tool call.

    Gets download info (URL or content) for a vault file.

    Args:
        args: Tool arguments (knowledge_id, file_id, output_path)
    """
    knowledge_id = args.get("knowledge_id")
    file_id = args.get("file_id")

    if not knowledge_id or not file_id:
        missing = []
        if not knowledge_id:
            missing.append("knowledge_id")
        if not file_id:
            missing.append("file_id")
        return ToolResponse(
            success=False,
            error=f"Missing required parameter(s): {', '.join(missing)}",
            error_code="missing_param",
        )

    try:
        client = await get_client()

        # Get file info from the list endpoint
        result = await client._request(
            "GET", f"/api/v1/vault/{knowledge_id}",
        )
        result = _unwrap_envelope(result)

        # Find the specific file
        files = result.get("files", [])
        target_file = None
        for f in files:
            if f.get("file_id") == file_id:
                target_file = f
                break

        if not target_file:
            return ToolResponse(
                success=False,
                error=f"File '{file_id}' not found in entry '{knowledge_id}'",
                error_code="file_not_found",
            )

        download_url = target_file.get("download_url", f"/api/v1/vault/{knowledge_id}/{file_id}/download")

        return ToolResponse(
            success=True,
            data={
                "knowledge_id": knowledge_id,
                "file_id": file_id,
                "filename": target_file.get("filename"),
                "mime_type": target_file.get("mime_type"),
                "size_bytes": target_file.get("size_bytes"),
                "download_url": download_url,
                "instructions": (
                    f"To download: GET {download_url}. "
                    f"For S3 backends, this returns a 307 redirect to a pre-signed URL. "
                    f"For local backends, the file content is streamed directly."
                ),
            },
        )

    except Exception as e:
        logger.error(f"Vault download failed: {e}")
        return ToolResponse(
            success=False,
            error=f"Vault download failed: {str(e)}",
            error_code="vault_download_error",
        )
