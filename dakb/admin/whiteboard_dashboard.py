"""
Whiteboard Admin Dashboard Route

Serves the whiteboard panel HTML template at /admin/whiteboard.

Version: 1.0
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

whiteboard_dashboard_router = APIRouter(tags=["Admin Dashboard"])

WHITEBOARD_TEMPLATE_PATH = Path(__file__).parent / "templates" / "whiteboard_panel.html"


@whiteboard_dashboard_router.get(
    "/admin/whiteboard",
    response_class=HTMLResponse,
    summary="Whiteboard Panel",
    description="Admin dashboard panel for the team whiteboard.",
)
async def get_whiteboard_panel() -> HTMLResponse:
    """
    Serve the whiteboard admin panel HTML page.

    Returns:
        HTML page for the whiteboard panel.
    """
    if not WHITEBOARD_TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail="Whiteboard panel template not found",
        )

    with open(WHITEBOARD_TEMPLATE_PATH) as f:
        html_content = f.read()

    return HTMLResponse(content=html_content)
