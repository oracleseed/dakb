"""
DAKB Knowledge Base CLI - Client Implementation

Provides command-line access to DAKB operations against a running gateway:
- Knowledge management (store, search, get, delete, stats)
- Messaging (send, inbox, read, broadcast)
- Session management (start, status, end, list, export)
- Voting (upvote, downvote, flag)
- Health check

This is the knowledge-base *client* CLI (`dakb-kb`). It is distinct from the
server-management CLI in ``dakb.cli.__init__`` (``dakb-server``), which starts
and stops the local services.

Version: 1.0.0

Installation:
    pip install click httpx python-dotenv

Usage:
    # Configure token
    export DAKB_AUTH_TOKEN="your-jwt-token"
    export DAKB_BASE_URL="http://localhost:3100"

    # Knowledge operations
    dakb-kb knowledge store --title "Title" --content "Content" --type pattern --category database
    dakb-kb knowledge search "mongodb connection"
    dakb-kb knowledge get k_20990101_abc123
    dakb-kb knowledge delete k_20990101_abc123

    # Messaging
    dakb-kb message send --to backend-agent --subject "Hello" --content "Message body"
    dakb-kb message inbox
    dakb-kb message read msg_123

    # Sessions
    dakb-kb session start --dir /path/to/workspace
    dakb-kb session status sess_123
    dakb-kb session end sess_123

    # Voting
    dakb-kb vote up k_20990101_abc123
    dakb-kb vote down k_20990101_abc123 --reason outdated
    dakb-kb vote flag k_20990101_abc123 --reason spam
"""

import asyncio
import json
import os

import click
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_BASE_URL = "http://localhost:3100"


def get_config():
    """Get CLI configuration from environment."""
    return {
        "base_url": os.getenv("DAKB_BASE_URL", DEFAULT_BASE_URL),
        "token": os.getenv("DAKB_AUTH_TOKEN", ""),
    }


# =============================================================================
# HTTP CLIENT
# =============================================================================


class DAKBCLIClient:
    """HTTP client for CLI operations."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        endpoint: str,
        json_data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make HTTP request to DAKB."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=f"{self.base_url}{endpoint}",
                json=json_data,
                params=params,
                headers=self._headers(),
            )

            if response.status_code == 401:
                raise click.ClickException(
                    "Authentication failed. Check DAKB_AUTH_TOKEN."
                )
            elif response.status_code == 404:
                raise click.ClickException("Resource not found.")
            elif response.status_code == 422:
                detail = response.json().get("detail", "Validation error")
                raise click.ClickException(f"Validation error: {detail}")
            elif response.status_code >= 400:
                detail = response.json().get("detail", "Request failed")
                raise click.ClickException(f"Error ({response.status_code}): {detail}")

            if response.status_code == 204:
                return {}

            return response.json()


def run_async(coro):
    """Run async coroutine synchronously."""
    return asyncio.run(coro)


def get_client() -> DAKBCLIClient:
    """Get configured CLI client."""
    config = get_config()
    if not config["token"]:
        raise click.ClickException(
            "DAKB_AUTH_TOKEN not set. Please set the environment variable."
        )
    return DAKBCLIClient(config["base_url"], config["token"])


# =============================================================================
# OUTPUT FORMATTERS
# =============================================================================


def format_json(data: dict, pretty: bool = True) -> str:
    """Format data as JSON."""
    if pretty:
        return json.dumps(data, indent=2, default=str)
    return json.dumps(data, default=str)


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format data as ASCII table."""
    if not rows:
        return "No data"

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    # Build table
    lines = []

    # Header
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("-+-".join("-" * w for w in widths))

    # Rows
    for row in rows:
        row_line = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))
        lines.append(row_line)

    return "\n".join(lines)


# =============================================================================
# CLI GROUPS
# =============================================================================


@click.group()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--url", envvar="DAKB_BASE_URL", default=DEFAULT_BASE_URL, help="DAKB Gateway URL")
@click.pass_context
def cli(ctx, output_json: bool, url: str):
    """
    DAKB Knowledge Base CLI.

    Interact with the Distributed Agent Knowledge Base from the command line.

    Configuration:
        export DAKB_AUTH_TOKEN="your-jwt-token"
        export DAKB_BASE_URL="http://localhost:3100"
    """
    ctx.ensure_object(dict)
    ctx.obj["output_json"] = output_json
    ctx.obj["base_url"] = url


# =============================================================================
# KNOWLEDGE COMMANDS
# =============================================================================


@cli.group()
def knowledge():
    """Knowledge base operations."""
    pass


@knowledge.command("store")
@click.option("--title", "-t", required=True, help="Knowledge title")
@click.option("--content", "-c", required=True, help="Knowledge content (or @filename)")
@click.option("--type", "content_type", required=True,
              type=click.Choice(["solution", "pattern", "fact", "procedure",
                                "configuration", "error_fix", "optimization", "best_practice"]),
              help="Content type")
@click.option("--category", "-g", required=True,
              type=click.Choice(["debugging", "architecture", "api", "database",
                                "ml", "trading", "deployment", "testing",
                                "documentation", "configuration"]),
              help="Category")
@click.option("--tags", help="Comma-separated tags")
@click.option("--access", default="public",
              type=click.Choice(["public", "restricted", "secret"]),
              help="Access level")
@click.option("--confidence", default=0.8, type=float, help="Confidence score (0-1)")
@click.pass_context
def knowledge_store(ctx, title, content, content_type, category, tags, access, confidence):
    """Store new knowledge entry."""
    client = get_client()

    # Handle file input for content
    if content.startswith("@"):
        filename = content[1:]
        try:
            with open(filename) as f:
                content = f.read()
        except FileNotFoundError:
            raise click.ClickException(f"File not found: {filename}")

    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    async def _store():
        return await client.request(
            "POST",
            "/api/v1/knowledge",
            json_data={
                "title": title,
                "content": content,
                "content_type": content_type,
                "category": category,
                "tags": tag_list,
                "access_level": access,
                "confidence": confidence,
            },
        )

    result = run_async(_store())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Created: {result.get('knowledge_id')}")
        click.echo(f"Title: {result.get('title')}")
        click.echo(f"Category: {result.get('category')}")


@knowledge.command("search")
@click.argument("query")
@click.option("--limit", "-k", default=10, type=int, help="Number of results")
@click.option("--category", "-g", help="Filter by category")
@click.option("--min-score", type=float, help="Minimum similarity score")
@click.pass_context
def knowledge_search(ctx, query, limit, category, min_score):
    """Search knowledge base semantically."""
    client = get_client()

    params = {"query": query, "k": limit}
    if category:
        params["category"] = category
    if min_score:
        params["min_score"] = min_score

    async def _search():
        return await client.request("GET", "/api/v1/knowledge/search", params=params)

    result = run_async(_search())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        results = result.get("results", [])
        if not results:
            click.echo("No results found.")
            return

        click.echo(f"Found {len(results)} results (query time: {result.get('search_time_ms', 0):.1f}ms)\n")

        for i, item in enumerate(results, 1):
            entry = item.get("knowledge", {})
            score = item.get("similarity_score", 0)

            click.echo(f"[{i}] {entry.get('title')} ({score:.2f})")
            click.echo(f"    ID: {entry.get('knowledge_id')}")
            click.echo(f"    Category: {entry.get('category')}")
            click.echo(f"    Tags: {', '.join(entry.get('tags', []))}")
            click.echo()


@knowledge.command("get")
@click.argument("knowledge_id")
@click.pass_context
def knowledge_get(ctx, knowledge_id):
    """Get knowledge entry by ID."""
    client = get_client()

    async def _get():
        return await client.request("GET", f"/api/v1/knowledge/{knowledge_id}")

    result = run_async(_get())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"ID: {result.get('knowledge_id')}")
        click.echo(f"Title: {result.get('title')}")
        click.echo(f"Type: {result.get('content_type')}")
        click.echo(f"Category: {result.get('category')}")
        click.echo(f"Tags: {', '.join(result.get('tags', []))}")
        click.echo(f"Access: {result.get('access_level')}")
        click.echo(f"Confidence: {result.get('confidence_score', 0):.2f}")
        click.echo(f"Quality: {result.get('quality_score', 0):.2f}")
        click.echo(f"Created: {result.get('created_at')}")
        click.echo(f"\nContent:\n{'-' * 40}")
        click.echo(result.get("content", ""))


@knowledge.command("delete")
@click.argument("knowledge_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
def knowledge_delete(ctx, knowledge_id, force):
    """Delete knowledge entry."""
    if not force:
        if not click.confirm(f"Delete {knowledge_id}?"):
            return

    client = get_client()

    async def _delete():
        return await client.request("DELETE", f"/api/v1/knowledge/{knowledge_id}")

    run_async(_delete())
    click.echo(f"Deleted: {knowledge_id}")


@knowledge.command("stats")
@click.pass_context
def knowledge_stats(ctx):
    """Get knowledge base statistics."""
    client = get_client()

    async def _stats():
        return await client.request("GET", "/api/v1/knowledge/stats")

    result = run_async(_stats())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Total entries: {result.get('total_entries', 0)}")
        click.echo(f"Indexed: {result.get('indexed_count', 0)}")
        click.echo(f"Expired: {result.get('expired_count', 0)}")
        click.echo("\nBy Category:")
        for cat, count in result.get("by_category", {}).items():
            click.echo(f"  {cat}: {count}")


# =============================================================================
# MESSAGE COMMANDS
# =============================================================================


@cli.group()
def message():
    """Messaging operations."""
    pass


@message.command("send")
@click.option("--to", "recipient_id", required=True, help="Recipient agent ID")
@click.option("--subject", "-s", required=True, help="Message subject")
@click.option("--content", "-c", required=True, help="Message content (or @filename)")
@click.option("--priority", "-p", default="normal",
              type=click.Choice(["low", "normal", "high", "urgent"]),
              help="Message priority")
@click.pass_context
def message_send(ctx, recipient_id, subject, content, priority):
    """Send a direct message to another agent."""
    client = get_client()

    # Handle file input
    if content.startswith("@"):
        filename = content[1:]
        try:
            with open(filename) as f:
                content = f.read()
        except FileNotFoundError:
            raise click.ClickException(f"File not found: {filename}")

    async def _send():
        return await client.request(
            "POST",
            "/api/v1/messages",
            json_data={
                "recipient_id": recipient_id,
                "message_type": "direct",
                "subject": subject,
                "content": content,
                "priority": priority,
            },
        )

    result = run_async(_send())
    msg = result.get("message", result)

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Message sent: {msg.get('message_id')}")
        click.echo(f"To: {recipient_id}")
        click.echo(f"Subject: {subject}")
        click.echo(f"Priority: {priority}")


@message.command("inbox")
@click.option("--status", type=click.Choice(["pending", "delivered", "read", "expired"]),
              help="Filter by status")
@click.option("--priority", type=click.Choice(["low", "normal", "high", "urgent"]),
              help="Filter by priority")
@click.option("--limit", "-n", default=20, type=int, help="Number of messages")
@click.pass_context
def message_inbox(ctx, status, priority, limit):
    """Get inbox messages."""
    client = get_client()

    params = {"page_size": limit}
    if status:
        params["status"] = status
    if priority:
        params["priority"] = priority

    async def _inbox():
        return await client.request("GET", "/api/v1/messages", params=params)

    result = run_async(_inbox())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        messages = result.get("messages", [])
        total = result.get("total", 0)

        if not messages:
            click.echo("No messages.")
            return

        click.echo(f"Showing {len(messages)} of {total} messages\n")

        for msg in messages:
            status_icon = {"pending": ".", "delivered": "*", "read": " ", "expired": "x"}.get(
                msg.get("status", ""), "?"
            )
            priority_icon = {"urgent": "!", "high": "+", "normal": " ", "low": "-"}.get(
                msg.get("priority", ""), " "
            )

            click.echo(f"[{status_icon}{priority_icon}] {msg.get('message_id')}")
            click.echo(f"    From: {msg.get('sender_id')}")
            click.echo(f"    Subject: {msg.get('subject')}")
            click.echo(f"    Date: {msg.get('created_at')}")
            click.echo()


@message.command("read")
@click.argument("message_id")
@click.pass_context
def message_read(ctx, message_id):
    """Read and mark message as read."""
    client = get_client()

    async def _read():
        # Get message
        msg = await client.request("GET", f"/api/v1/messages/{message_id}")
        # Mark as read
        await client.request("POST", f"/api/v1/messages/{message_id}/read")
        return msg

    result = run_async(_read())
    msg = result.get("message", result)

    if ctx.obj.get("output_json"):
        click.echo(format_json(msg))
    else:
        click.echo(f"From: {msg.get('sender_id')}")
        click.echo(f"To: {msg.get('recipient_id')}")
        click.echo(f"Subject: {msg.get('subject')}")
        click.echo(f"Priority: {msg.get('priority')}")
        click.echo(f"Date: {msg.get('created_at')}")
        click.echo(f"\n{'-' * 40}")
        click.echo(msg.get("content", ""))


@message.command("broadcast")
@click.option("--subject", "-s", required=True, help="Broadcast subject")
@click.option("--content", "-c", required=True, help="Broadcast content")
@click.option("--priority", "-p", default="normal",
              type=click.Choice(["low", "normal", "high", "urgent"]),
              help="Priority level")
@click.pass_context
def message_broadcast(ctx, subject, content, priority):
    """Send broadcast message to all agents."""
    client = get_client()

    async def _broadcast():
        return await client.request(
            "POST",
            "/api/v1/messages/broadcast",
            json_data={
                "subject": subject,
                "content": content,
                "priority": priority,
            },
        )

    result = run_async(_broadcast())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Broadcast sent: {result.get('message_id')}")
        click.echo(f"Recipients: {result.get('recipients_count', 0)}")


# =============================================================================
# SESSION COMMANDS
# =============================================================================


@cli.group()
def session():
    """Session management operations."""
    pass


@session.command("start")
@click.option("--dir", "working_dir", required=True, help="Working directory path")
@click.option("--task", help="Task description")
@click.option("--timeout", default=30, type=int, help="Timeout in minutes")
@click.pass_context
def session_start(ctx, working_dir, task, timeout):
    """Start a new session."""
    client = get_client()

    async def _start():
        return await client.request(
            "POST",
            "/api/v1/sessions",
            json_data={
                "working_directory": working_dir,
                "task_description": task,
                "timeout_minutes": timeout,
            },
        )

    result = run_async(_start())
    sess = result.get("session", result)

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Session started: {sess.get('session_id')}")
        click.echo(f"Directory: {sess.get('working_directory')}")
        click.echo(f"Status: {sess.get('status')}")
        click.echo(f"Timeout: {timeout} minutes")


@session.command("status")
@click.argument("session_id")
@click.pass_context
def session_status(ctx, session_id):
    """Get session status."""
    client = get_client()

    async def _status():
        return await client.request("GET", f"/api/v1/sessions/{session_id}")

    result = run_async(_status())
    sess = result.get("session", result)

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"ID: {sess.get('session_id')}")
        click.echo(f"Agent: {sess.get('agent_id')}")
        click.echo(f"Status: {sess.get('status')}")
        click.echo(f"Directory: {sess.get('working_directory')}")
        click.echo(f"Task: {sess.get('task_description', 'N/A')}")
        click.echo(f"Created: {sess.get('created_at')}")
        click.echo(f"Last Activity: {sess.get('last_activity_at')}")


@session.command("end")
@click.argument("session_id")
@click.option("--status", "end_status", default="completed",
              type=click.Choice(["completed", "abandoned"]),
              help="Final status")
@click.pass_context
def session_end(ctx, session_id, end_status):
    """End a session."""
    client = get_client()

    async def _end():
        return await client.request(
            "POST",
            f"/api/v1/sessions/{session_id}/end",
            json_data={"status": end_status},
        )

    result = run_async(_end())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Session ended: {session_id}")
        click.echo(f"Status: {end_status}")


@session.command("list")
@click.option("--status", type=click.Choice(["active", "paused", "completed", "abandoned"]),
              help="Filter by status")
@click.option("--limit", "-n", default=20, type=int, help="Number of sessions")
@click.pass_context
def session_list(ctx, status, limit):
    """List sessions."""
    client = get_client()

    params = {"page_size": limit}
    if status:
        params["status"] = status

    async def _list():
        return await client.request("GET", "/api/v1/sessions", params=params)

    result = run_async(_list())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        sessions = result.get("sessions", [])
        if not sessions:
            click.echo("No sessions found.")
            return

        click.echo(f"Showing {len(sessions)} sessions\n")

        for sess in sessions:
            status_icon = {
                "active": "*",
                "paused": "P",
                "completed": "+",
                "abandoned": "x",
            }.get(sess.get("status", ""), "?")

            click.echo(f"[{status_icon}] {sess.get('session_id')}")
            click.echo(f"    Agent: {sess.get('agent_id')}")
            click.echo(f"    Dir: {sess.get('working_directory')}")
            click.echo()


@session.command("export")
@click.argument("session_id")
@click.option("--output", "-o", help="Output file (default: stdout)")
@click.pass_context
def session_export(ctx, session_id, output):
    """Export session for handoff."""
    client = get_client()

    async def _export():
        return await client.request(
            "POST",
            f"/api/v1/sessions/{session_id}/export",
            json_data={
                "include_git_context": True,
                "include_patch_bundle": True,
            },
        )

    result = run_async(_export())

    if output:
        with open(output, "w") as f:
            json.dump(result, f, indent=2, default=str)
        click.echo(f"Exported to: {output}")
    else:
        click.echo(format_json(result))


# =============================================================================
# VOTE COMMANDS
# =============================================================================


@cli.group()
def vote():
    """Voting operations."""
    pass


@vote.command("up")
@click.argument("knowledge_id")
@click.option("--comment", help="Optional comment")
@click.pass_context
def vote_up(ctx, knowledge_id, comment):
    """Upvote a knowledge entry as helpful."""
    client = get_client()

    data = {"vote": "helpful"}
    if comment:
        data["comment"] = comment

    async def _vote():
        return await client.request(
            "POST",
            f"/api/v1/knowledge/{knowledge_id}/vote",
            json_data=data,
        )

    result = run_async(_vote())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Upvoted: {knowledge_id}")


@vote.command("down")
@click.argument("knowledge_id")
@click.option("--reason", default="unhelpful",
              type=click.Choice(["unhelpful", "outdated", "incorrect"]),
              help="Reason for downvote")
@click.option("--comment", help="Optional comment")
@click.pass_context
def vote_down(ctx, knowledge_id, reason, comment):
    """Downvote a knowledge entry."""
    client = get_client()

    data = {"vote": reason}
    if comment:
        data["comment"] = comment

    async def _vote():
        return await client.request(
            "POST",
            f"/api/v1/knowledge/{knowledge_id}/vote",
            json_data=data,
        )

    result = run_async(_vote())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Downvoted ({reason}): {knowledge_id}")


@vote.command("flag")
@click.argument("knowledge_id")
@click.option("--reason", required=True,
              type=click.Choice(["duplicate", "inappropriate", "spam", "outdated", "inaccurate", "other"]),
              help="Reason for flagging")
@click.option("--comment", help="Additional details")
@click.pass_context
def vote_flag(ctx, knowledge_id, reason, comment):
    """Flag knowledge entry for review."""
    client = get_client()

    data = {"knowledge_id": knowledge_id, "reason": reason}
    if comment:
        data["comment"] = comment

    async def _flag():
        return await client.request(
            "POST",
            "/api/v1/moderation/flag",
            json_data=data,
        )

    result = run_async(_flag())

    if ctx.obj.get("output_json"):
        click.echo(format_json(result))
    else:
        click.echo(f"Flagged for review: {knowledge_id}")
        click.echo(f"Reason: {reason}")


# =============================================================================
# HEALTH COMMAND
# =============================================================================


@cli.command("health")
@click.pass_context
def health(ctx):
    """Check DAKB Gateway health."""
    config = get_config()

    async def _health():
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{config['base_url']}/health")
            return response.json()

    try:
        result = run_async(_health())

        if ctx.obj.get("output_json"):
            click.echo(format_json(result))
        else:
            click.echo(f"Status: {result.get('status', 'unknown')}")
            click.echo(f"Service: {result.get('service', 'unknown')}")
            click.echo(f"Version: {result.get('version', 'unknown')}")
            click.echo(f"MongoDB: {result.get('mongodb', 'unknown')}")
            click.echo(f"Embeddings: {result.get('embedding_service', 'unknown')}")
    except Exception as e:
        raise click.ClickException(f"Health check failed: {e}")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


def main():
    """Main entry point for the knowledge-base client CLI."""
    cli(obj={})


if __name__ == "__main__":
    main()
