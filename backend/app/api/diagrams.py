from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.diagrams import asset_path, get_diagram

router = APIRouter(prefix="/api/diagrams", tags=["diagrams"])


@router.get("/{diagram_id}/asset")
def diagram_asset(diagram_id: str) -> FileResponse:
    diagram = get_diagram(diagram_id)
    path = asset_path(diagram) if diagram else None
    if path is None:
        raise HTTPException(status_code=404, detail="Diagram asset not found")
    return FileResponse(path, media_type=diagram.get("mime_type") or "image/png", filename=f"diagram-{diagram_id}.png")
