from __future__ import annotations

import io
import json
import mimetypes
import posixpath
import sqlite3
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from ..backup_service import BackupService
from ..db import row_to_dict, rows_to_dicts
from ..runtime import (
    course_label_or_404,
    file_operation_status,
    make_canvas_client,
    make_extractor,
    state,
)
from ..schemas import FileSelectionIn
from ..services.preview_service import ensure_pdf_preview
from ..sync_service import SyncService

router = APIRouter()


def _file_row_or_404(course_id: int, file_id: int) -> dict[str, Any]:
    with state().db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, course_id, display_name, filename, content_type, size, updated_at,
                   local_path, backup_status, backup_error, extraction_status,
                   extraction_error, extracted_text_path
            FROM files
            WHERE course_id = ? AND id = ?
            """,
            (course_id, file_id),
        ).fetchone()
    item = row_to_dict(row)
    if item is None:
        raise HTTPException(status_code=404, detail="File is not synced for this course")
    return item


def _local_file_path_or_404(file_row: dict[str, Any]) -> Path:
    local_path = file_row.get("local_path")
    if not local_path or file_row.get("backup_status") != "downloaded":
        raise HTTPException(status_code=409, detail="File has not been downloaded to local cache")
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Cached file is missing on disk")
    try:
        data_root = state().settings.data_dir.resolve()
        path.resolve().relative_to(data_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Cached file path is outside the configured data directory") from exc
    return path


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    return f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"


def _normalize_folder_path(value: str | list[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        raw_parts = [str(part) for part in value]
    else:
        raw_parts = str(value).replace("\\", "/").split("/")
    parts = [part.strip() for part in raw_parts if part and part.strip()]
    if parts and parts[0].lower() == "course files":
        parts = parts[1:]
    return "/" + "/".join(parts) if parts else "/"


def _folder_path_from_raw_json(raw_json: str | None) -> str | None:
    if not raw_json:
        return None
    try:
        metadata = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    stored_path = metadata.get("canvas_folder_path")
    if isinstance(stored_path, (str, list)):
        return _normalize_folder_path(stored_path)
    folder = metadata.get("folder") if isinstance(metadata.get("folder"), dict) else None
    if folder:
        full_name = folder.get("full_name") or folder.get("name")
        if isinstance(full_name, str):
            return _normalize_folder_path(full_name)
    return None


def _folder_path_from_local_path(course_id: int, local_path: str | None) -> str | None:
    if not local_path:
        return None
    parts = [part for part in str(local_path).replace("\\", "/").split("/") if part]
    marker = f"course_{course_id}"
    try:
        marker_index = parts.index(marker)
    except ValueError:
        return "/"
    folder_parts = parts[marker_index + 1 : -1]
    return _normalize_folder_path(folder_parts)


@router.get("/api/courses/{course_id}/files")
async def files(course_id: int) -> list[dict[str, Any]]:
    def query() -> list[dict[str, Any]]:
        with state().db.connect() as conn:
            return list_course_files_from_db(conn, course_id)

    return await run_in_threadpool(query)


def list_course_files_from_db(conn: sqlite3.Connection, course_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, display_name, filename, content_type, size, updated_at,
               local_path, sha256, backup_status, backup_error, downloaded_at,
               extraction_status, extraction_error, outline_json, extracted_at,
               raw_json
        FROM files
        WHERE course_id = ?
        ORDER BY updated_at DESC, display_name
        """,
        (course_id,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        if item.get("outline_json"):
            try:
                item["outline"] = json.loads(item["outline_json"])
            except json.JSONDecodeError:
                item["outline"] = []
        else:
            item["outline"] = []
        item.pop("outline_json", None)
        item["folder_path"] = (
            _folder_path_from_local_path(course_id, item.get("local_path"))
            or _folder_path_from_raw_json(item.get("raw_json"))
            or "/"
        )
        item.pop("raw_json", None)
    return items


@router.get("/api/courses/{course_id}/files/{file_id}/download")
async def download_file(course_id: int, file_id: int) -> FileResponse:
    def resolve() -> tuple[Path, str, str]:
        file_row = _file_row_or_404(course_id, file_id)
        path = _local_file_path_or_404(file_row)
        filename = file_row.get("display_name") or path.name
        media_type = file_row.get("content_type") or "application/octet-stream"
        return path, filename, media_type

    path, filename, media_type = await run_in_threadpool(resolve)
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/api/courses/{course_id}/files/download")
async def download_files_zip(course_id: int, payload: FileSelectionIn) -> Response:
    if not payload.file_ids:
        raise HTTPException(status_code=400, detail="Select at least one file")

    def build() -> bytes:
        with state().db.connect() as conn:
            placeholders = ",".join("?" for _ in payload.file_ids)
            rows = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT id, display_name, filename, content_type, local_path, backup_status
                    FROM files
                    WHERE course_id = ? AND id IN ({placeholders})
                    ORDER BY display_name
                    """,
                    [course_id, *payload.file_ids],
                ).fetchall()
            )
        if not rows:
            raise HTTPException(status_code=404, detail="No selected files were found")

        archive = io.BytesIO()
        added = 0
        used_names: set[str] = set()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for item in rows:
                try:
                    path = _local_file_path_or_404(item)
                except HTTPException:
                    continue
                filename = item.get("display_name") or item.get("filename") or path.name
                arcname = posixpath.basename(filename).strip() or f"file-{item['id']}"
                stem = Path(arcname).stem
                suffix = Path(arcname).suffix
                counter = 2
                while arcname.lower() in used_names:
                    arcname = f"{stem}-{counter}{suffix}"
                    counter += 1
                used_names.add(arcname.lower())
                bundle.write(path, arcname)
                added += 1
        if added == 0:
            raise HTTPException(status_code=409, detail="Selected files are not available in local cache")
        archive.seek(0)
        return archive.getvalue()

    data = await run_in_threadpool(build)
    filename = f"course_{course_id}_files.zip"
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.get("/api/courses/{course_id}/files/{file_id}/preview")
async def preview_file(course_id: int, file_id: int):
    def resolve() -> tuple[Path, str]:
        file_row = _file_row_or_404(course_id, file_id)
        path = _local_file_path_or_404(file_row)
        filename = file_row.get("display_name") or path.name
        media_type = file_row.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        preview_path = ensure_pdf_preview(state().settings.data_dir, course_id, file_id, file_row, path, media_type)
        preview_name = (
            filename
            if preview_path == path and path.suffix.lower() == ".pdf"
            else f"{Path(filename).stem or 'preview'}.pdf"
        )
        return preview_path, preview_name

    preview_path, preview_name = await run_in_threadpool(resolve)
    return FileResponse(
        preview_path,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(preview_name, "inline")},
    )


@router.post("/api/courses/{course_id}/files/{file_id}/extract")
async def extract_file(course_id: int, file_id: int) -> dict[str, Any]:
    file_row = _file_row_or_404(course_id, file_id)
    _local_file_path_or_404(file_row)
    counts = await make_extractor().extract_files(course_id, [file_id])
    return {"status": "completed", "counts": counts}


@router.post("/api/courses/{course_id}/files/{file_id}/backup")
async def backup_file(course_id: int, file_id: int) -> dict[str, Any]:
    return await backup_selected_files(course_id, FileSelectionIn(file_ids=[file_id]))


@router.post("/api/courses/{course_id}/files/backup")
async def backup_selected_files(course_id: int, payload: FileSelectionIn) -> dict[str, Any]:
    if not payload.file_ids:
        raise HTTPException(status_code=400, detail="Select at least one file")
    course_name = course_label_or_404(course_id)
    state().db.add_event(
        category="file",
        action="file_backup_started",
        status="running",
        title="Selected file backup started",
        course_id=course_id,
        course_name=course_name,
        metadata={"file_count": len(payload.file_ids)},
    )
    try:
        async with make_canvas_client() as canvas:
            backup = BackupService(state().db, canvas, state().settings.data_dir, min_free_bytes=state().settings.backup_min_free_bytes)
            backup_counts = await backup.backup_files(course_id, payload.file_ids)
        extraction_counts = await make_extractor().extract_files(course_id, payload.file_ids)
    except Exception as exc:
        state().db.add_event(
            category="file",
            action="file_backup_failed",
            status="failed",
            title="Selected file backup failed",
            course_id=course_id,
            course_name=course_name,
            message=f"{exc.__class__.__name__}: {exc}",
        )
        raise
    state().db.add_event(
        category="file",
        action="file_backup_completed",
        status=file_operation_status(backup_counts, extraction_counts),
        title="Selected file backup completed",
        course_id=course_id,
        course_name=course_name,
        metadata={"backup": backup_counts, "extraction": extraction_counts},
    )
    return {
        "status": "completed",
        "backup": backup_counts,
        "extraction": extraction_counts,
    }


@router.post("/api/courses/{course_id}/files/sync")
async def sync_course_files(course_id: int) -> dict[str, Any]:
    if state().file_sync_lock.locked():
        raise HTTPException(status_code=409, detail="A file sync job is already running")
    course_name = course_label_or_404(course_id)
    async with state().file_sync_lock:
        state().db.add_event(
            category="file",
            action="file_sync_started",
            status="running",
            title="Course file sync started",
            course_id=course_id,
            course_name=course_name,
        )
        try:
            async with make_canvas_client() as canvas:
                backup = BackupService(state().db, canvas, state().settings.data_dir, min_free_bytes=state().settings.backup_min_free_bytes)
                service = SyncService(
                    state().db,
                    canvas,
                    backup,
                    make_extractor(),
                    is_cancelled=state().file_sync_cancel_event.is_set,
                )
                result = await service.sync_course_files(course_id)
            state().db.add_event(
                category="file",
                action="file_sync_completed",
                status=file_operation_status(result["backup"], result["extraction"]),
                title="Course file sync completed",
                course_id=course_id,
                course_name=course_name,
                metadata=result,
            )
            return result
        except Exception as exc:
            state().db.add_event(
                category="file",
                action="file_sync_failed",
                status="failed",
                title="Course file sync failed",
                course_id=course_id,
                course_name=course_name,
                message=f"{exc.__class__.__name__}: {exc}",
            )
            raise
