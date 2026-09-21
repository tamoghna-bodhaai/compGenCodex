from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.services.exports import ExportNotFoundError, list_exports, resolve_export

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("")
def get_exports(paper_id: str | None = None, kind: str | None = None) -> dict:
    try:
        return {"items": list_exports(paper_id=paper_id, kind=kind)}
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{export_id}/download")
def download_export(export_id: str) -> FileResponse:
    try:
        item, path = resolve_export(export_id)
        return FileResponse(path, media_type=item["media_type"], filename=item["filename"])
    except ExportNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
