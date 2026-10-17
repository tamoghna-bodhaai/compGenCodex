from __future__ import annotations

from fastapi import APIRouter, HTTPException
from app.core.auth import current_user_context
from app.db.database import get_connection
from fastapi.responses import FileResponse

from app.services.diagrams import asset_path, get_diagram

router = APIRouter(prefix="/api/diagrams", tags=["diagrams"])


@router.get("/{diagram_id}/asset")
def diagram_asset(diagram_id: str) -> FileResponse:
    diagram = get_diagram(diagram_id)
    actor = current_user_context.get()
    if diagram and actor and actor["role"] != "admin":
        if diagram.get("seed_question_id"):
            with get_connection() as connection:
                row = connection.execute("SELECT published FROM questions WHERE id=?", (diagram["seed_question_id"],)).fetchone()
            if not row or not row["published"]:
                raise HTTPException(404, "Diagram asset not found")
        elif diagram.get("paper_question_id"):
            with get_connection() as connection:
                row = connection.execute("SELECT p.owner_id FROM papers p JOIN paper_questions q ON q.paper_id=p.id WHERE q.id=?", (diagram["paper_question_id"],)).fetchone()
            if not row or row["owner_id"] != actor["id"]:
                raise HTTPException(404, "Diagram asset not found")
    path = asset_path(diagram) if diagram else None
    if path is None:
        raise HTTPException(status_code=404, detail="Diagram asset not found")
    return FileResponse(path, media_type=diagram.get("mime_type") or "image/png", filename=f"diagram-{diagram_id}.png")
