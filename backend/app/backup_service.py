from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .canvas_client import CanvasReadOnlyClient
from .db import Database, utc_now


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip()
    if cleaned in {".", ".."}:
        return "file"
    return cleaned[:160] or "file"


def safe_path_segment(name: str) -> str:
    return safe_filename(name)


class BackupService:
    def __init__(self, db: Database, canvas: CanvasReadOnlyClient, data_dir: Path):
        self.db = db
        self.canvas = canvas
        self.data_dir = data_dir
        self._folder_cache: dict[int, dict[str, Any]] = {}

    async def backup_course_files(
        self,
        course_id: int,
        *,
        check_cancelled: Callable[[], None] | None = None,
        on_progress: Callable[[int, int, str | None], None] | None = None,
    ) -> dict[str, int]:
        return await self.backup_files(
            course_id,
            check_cancelled=check_cancelled,
            on_progress=on_progress,
        )

    async def backup_files(
        self,
        course_id: int,
        file_ids: list[int] | None = None,
        *,
        check_cancelled: Callable[[], None] | None = None,
        on_progress: Callable[[int, int, str | None], None] | None = None,
    ) -> dict[str, int]:
        counts = {"downloaded": 0, "skipped": 0, "failed": 0}
        with self.db.connect() as conn:
            args: list[int] = [course_id]
            file_filter = ""
            if file_ids is not None:
                if not file_ids:
                    return counts
                placeholders = ",".join("?" for _ in file_ids)
                file_filter = f" AND id IN ({placeholders})"
                args.extend(file_ids)
            rows = conn.execute(
                f"""
                SELECT * FROM files
                WHERE course_id = ?
                {file_filter}
                ORDER BY display_name
                """,
                args,
            ).fetchall()

        total = len(rows)
        course_name = self._course_label(course_id)
        for index, row in enumerate(rows, start=1):
            if check_cancelled:
                check_cancelled()
            if on_progress:
                on_progress(index - 1, total, row["display_name"])
            archived: Path | None = None
            try:
                metadata = await self.canvas.get_json(f"/api/v1/files/{row['id']}")
                download_url = metadata.get("url")
                if not download_url:
                    raise RuntimeError("Canvas file metadata did not include a download URL")
                canvas_updated_at = metadata.get("updated_at") or row["updated_at"]
                expected_size = metadata.get("size") or row["size"]

                destination = await self._destination_for_file(course_id, row, metadata)
                if self._is_current(row, destination):
                    counts["skipped"] += 1
                    self.db.add_event(
                        category="file",
                        action="file_downloaded",
                        status="success",
                        title="File download skipped",
                        course_id=course_id,
                        course_name=course_name,
                        item_id=row["id"],
                        item_name=row["display_name"],
                        message="Local cache is already current.",
                        metadata={"outcome": "skipped"},
                    )
                    if on_progress:
                        on_progress(index, total, row["display_name"])
                    continue

                archived = self._archive_previous(row, destination)
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE files
                        SET backup_status = 'downloading', backup_error = NULL
                        WHERE id = ?
                        """,
                        (row["id"],),
                    )
                download_options = {"check_cancelled": check_cancelled} if check_cancelled else {}
                sha256, byte_count = await self.canvas.download_to_file(
                    download_url,
                    destination,
                    **download_options,
                )
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE files
                        SET local_path = ?,
                            sha256 = ?,
                            backup_status = 'downloaded',
                            backup_error = NULL,
                            downloaded_at = ?,
                            downloaded_canvas_updated_at = ?,
                            size = COALESCE(size, ?)
                        WHERE id = ?
                        """,
                        (
                            str(destination),
                            sha256,
                            utc_now(),
                            canvas_updated_at,
                            expected_size or byte_count,
                            row["id"],
                        ),
                    )
                counts["downloaded"] += 1
                self.db.add_event(
                    category="file",
                    action="file_downloaded",
                    status="success",
                    title="File downloaded",
                    course_id=course_id,
                    course_name=course_name,
                    item_id=row["id"],
                    item_name=row["display_name"],
                    metadata={"outcome": "downloaded", "size": expected_size or byte_count},
                )
                if on_progress:
                    on_progress(index, total, row["display_name"])
            except Exception as exc:  # Keep one bad file from failing the course.
                # The re-download failed: bring back the last good copy that
                # _archive_previous moved away so local_path stays valid.
                self._restore_archived(row, archived)
                cancel_exc: Exception | None = None
                if check_cancelled:
                    try:
                        check_cancelled()
                    except Exception as raised:
                        cancel_exc = raised
                error_text = f"{exc.__class__.__name__}: {exc}"
                has_local_copy = bool(
                    row["backup_status"] == "downloaded"
                    and row["local_path"]
                    and Path(row["local_path"]).exists()
                )
                with self.db.connect() as conn:
                    if cancel_exc is not None:
                        # Cancelled mid-download: restore the pre-download status
                        # instead of leaving the row stuck on 'downloading'.
                        prior_status = row["backup_status"] or "pending"
                        if prior_status == "downloading":
                            prior_status = "pending"
                        conn.execute(
                            """
                            UPDATE files
                            SET backup_status = ?, backup_error = ?
                            WHERE id = ?
                            """,
                            (prior_status, row["backup_error"], row["id"]),
                        )
                    elif has_local_copy:
                        # The stale-but-valid copy keeps serving downloads and
                        # previews; record why the refresh failed alongside it.
                        conn.execute(
                            """
                            UPDATE files
                            SET backup_status = 'downloaded', backup_error = ?
                            WHERE id = ?
                            """,
                            (f"Refresh failed; keeping the previous local copy. {error_text}", row["id"]),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE files
                            SET backup_status = 'fail_download', backup_error = ?
                            WHERE id = ?
                            """,
                            (error_text, row["id"]),
                        )
                if cancel_exc is not None:
                    raise cancel_exc
                counts["failed"] += 1
                self.db.add_event(
                    category="file",
                    action="file_downloaded",
                    status="failed",
                    title="File download failed",
                    course_id=course_id,
                    course_name=course_name,
                    item_id=row["id"],
                    item_name=row["display_name"],
                    message=f"{exc.__class__.__name__}: {exc}",
                    metadata={"outcome": "failed"},
                )
                if on_progress:
                    on_progress(index, total, row["display_name"])
        return counts

    def _course_label(self, course_id: int) -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT name, course_code FROM courses WHERE id = ?",
                (course_id,),
            ).fetchone()
        if not row:
            return f"course_{course_id}"
        return row["course_code"] or row["name"] or f"course_{course_id}"

    async def _destination_for_file(self, course_id: int, row, metadata: dict[str, Any]) -> Path:
        destination = self.data_dir / "canvas" / f"course_{course_id}"
        for segment in await self.canvas_folder_segments(metadata):
            destination /= segment
        return destination / safe_filename(
            metadata.get("display_name") or row["display_name"] or row["filename"]
        )

    async def canvas_folder_segments(self, metadata: dict[str, Any]) -> list[str]:
        return await self._canvas_folder_segments(metadata)

    async def _canvas_folder_segments(self, metadata: dict[str, Any]) -> list[str]:
        folder = metadata.get("folder") if isinstance(metadata.get("folder"), dict) else None
        folder_id = metadata.get("folder_id")
        if folder is None and folder_id is not None:
            folder = await self._get_folder(int(folder_id))

        full_name = (folder or {}).get("full_name") or (folder or {}).get("name") or ""
        raw_segments = [segment for segment in full_name.split("/") if segment]
        if raw_segments and raw_segments[0].lower() == "course files":
            raw_segments = raw_segments[1:]
        return [safe_path_segment(segment) for segment in raw_segments]

    async def _get_folder(self, folder_id: int) -> dict[str, Any]:
        if folder_id not in self._folder_cache:
            self._folder_cache[folder_id] = await self.canvas.get_json(f"/api/v1/folders/{folder_id}")
        return self._folder_cache[folder_id]

    def _is_current(self, row, destination: Path) -> bool:
        local_path = row["local_path"]
        if not local_path or not row["sha256"]:
            return False
        path = Path(local_path)
        if not path.exists():
            return False
        if path.resolve() != destination.resolve():
            return False
        if row["size"] is not None and path.stat().st_size != int(row["size"]):
            return False
        return row["downloaded_canvas_updated_at"] == row["updated_at"]

    def _archive_previous(self, row, destination: Path) -> Path | None:
        """Move the previous local copy into .versions; return its archive path."""
        local_path = row["local_path"]
        if not local_path:
            return None
        previous = Path(local_path)
        if not previous.exists():
            return None
        version_dir = destination.parent / ".versions"
        version_dir.mkdir(parents=True, exist_ok=True)
        stamp = (row["downloaded_at"] or utc_now()).replace(":", "").replace("+", "_")
        archive = version_dir / f"{row['id']}_{stamp}_{safe_filename(row['filename'])}"
        shutil.move(str(previous), str(archive))
        return archive

    def _restore_archived(self, row, archive: Path | None) -> None:
        """Best-effort restore of the archived copy after a failed re-download."""
        if archive is None or not archive.exists() or not row["local_path"]:
            return
        previous = Path(row["local_path"])
        try:
            if not previous.exists():
                previous.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archive), str(previous))
        except OSError:
            # Leave the copy under .versions rather than failing the error path.
            pass
