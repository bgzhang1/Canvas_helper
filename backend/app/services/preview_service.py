"""Browser-facing file preview generation (PDF/office/image/text -> PDF)."""

from __future__ import annotations

import html
import io
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from fastapi.responses import Response

TEXT_PREVIEW_EXTENSIONS = {".txt", ".md", ".csv", ".py", ".java", ".c", ".cpp", ".js", ".ts"}
OFFICE_PREVIEW_EXTENSIONS = {
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".rtf",
    ".odt",
    ".odp",
    ".ods",
}
IMAGE_PREVIEW_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def html_preview(title: str, body: str, *, subtitle: str = "") -> Response:
    escaped_title = html.escape(title)
    escaped_subtitle = html.escape(subtitle)
    escaped_body = html.escape(body or "No preview text is available.")
    document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      :root {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #111; background: #fff; }}
      body {{ margin: 0; padding: 32px; line-height: 1.55; }}
      header {{ border-bottom: 1px solid #111; margin-bottom: 24px; padding-bottom: 16px; }}
      h1 {{ font-size: 20px; margin: 0 0 8px; }}
      p {{ color: #555; margin: 0; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
      pre {{ white-space: pre-wrap; word-break: break-word; font-size: 13px; margin: 0; }}
    </style>
  </head>
  <body>
    <header>
      <h1>{escaped_title}</h1>
      <p>{escaped_subtitle}</p>
    </header>
    <pre>{escaped_body}</pre>
  </body>
</html>"""
    return Response(document, media_type="text/html; charset=utf-8")


def read_extracted_text(file_row: dict[str, Any], limit: int = 120_000) -> str:
    extracted_path = file_row.get("extracted_text_path")
    if not extracted_path:
        return ""
    path = Path(extracted_path)
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def preview_pdf_cache_path(data_dir: Path, course_id: int, file_id: int) -> Path:
    return data_dir / "previews" / f"course_{course_id}" / f"{file_id}.pdf"


def _preview_pdf_is_current(preview_path: Path, source_path: Path) -> bool:
    return (
        preview_path.exists()
        and preview_path.is_file()
        and preview_path.stat().st_size > 0
        and preview_path.stat().st_mtime >= source_path.stat().st_mtime
    )


def _replace_file(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    source.replace(destination)


def _write_pdf_atomically(destination: Path, writer) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=destination.parent)
    temp_path = Path(handle.name)
    handle.close()
    temp_path.unlink()
    try:
        writer(temp_path)
        _replace_file(temp_path, destination)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _write_text_pdf(destination: Path, title: str, body: str) -> None:
    text = body[:120_000] if body else "No preview text is available."

    def writer(temp_path: Path) -> None:
        import fitz

        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            x = 42
            y = 48
            line_height = 13
            page.insert_text((x, y), title[:120], fontsize=13, fontname="helv")
            y += 28
            for raw_line in text.splitlines() or [""]:
                wrapped_lines = textwrap.wrap(
                    raw_line,
                    width=92,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                ) or [""]
                for line in wrapped_lines:
                    if y > 805:
                        page = doc.new_page(width=595, height=842)
                        y = 48
                    page.insert_text((x, y), line, fontsize=9, fontname="cour")
                    y += line_height
            doc.save(temp_path)
        finally:
            doc.close()

    _write_pdf_atomically(destination, writer)


def _write_image_pdf(source_path: Path, destination: Path) -> None:
    def writer(temp_path: Path) -> None:
        import fitz
        from PIL import Image

        with Image.open(source_path) as image:
            width, height = image.size
            converted = image.convert("RGB")
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG")

        doc = fitz.open()
        try:
            page = doc.new_page(width=max(width, 1), height=max(height, 1))
            page.insert_image(page.rect, stream=buffer.getvalue())
            doc.save(temp_path)
        finally:
            doc.close()

    _write_pdf_atomically(destination, writer)


def _find_office_pdf_converter() -> str | None:
    for command in ("soffice.com", "soffice", "libreoffice"):
        found = shutil.which(command)
        if found:
            return found
    candidates = [
        Path("C:/Program Files/LibreOffice/program/soffice.com"),
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.com"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _convert_office_to_pdf(source_path: Path, destination: Path) -> bool:
    converter = _find_office_pdf_converter()
    if not converter:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp_dir:
        result = subprocess.run(
            [
                converter,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"LibreOffice PDF conversion failed: {detail[:600] or result.returncode}")
        generated = Path(temp_dir) / f"{source_path.stem}.pdf"
        if not generated.exists():
            matches = list(Path(temp_dir).glob("*.pdf"))
            if not matches:
                raise RuntimeError("LibreOffice did not produce a PDF preview")
            generated = matches[0]
        _replace_file(generated, destination)
    return True


def ensure_pdf_preview(
    data_dir: Path,
    course_id: int,
    file_id: int,
    file_row: dict[str, Any],
    source_path: Path,
    media_type: str,
) -> Path:
    suffix = source_path.suffix.lower()
    if media_type == "application/pdf" or suffix == ".pdf":
        return source_path

    preview_path = preview_pdf_cache_path(data_dir, course_id, file_id)
    if _preview_pdf_is_current(preview_path, source_path):
        return preview_path

    filename = file_row.get("display_name") or source_path.name
    conversion_error: str | None = None
    if suffix in OFFICE_PREVIEW_EXTENSIONS:
        try:
            if _convert_office_to_pdf(source_path, preview_path):
                return preview_path
        except Exception as exc:
            conversion_error = f"{exc.__class__.__name__}: {exc}"

    if media_type.startswith("image/") or suffix in IMAGE_PREVIEW_EXTENSIONS:
        _write_image_pdf(source_path, preview_path)
        return preview_path

    if media_type.startswith("text/") or suffix in TEXT_PREVIEW_EXTENSIONS:
        text = source_path.read_text(encoding="utf-8", errors="replace")
        _write_text_pdf(preview_path, filename, text)
        return preview_path

    extracted = read_extracted_text(file_row)
    if extracted:
        fallback = extracted
        if conversion_error:
            fallback = f"Original-layout PDF conversion failed.\n{conversion_error}\n\n{extracted}"
        _write_text_pdf(preview_path, filename, fallback)
        return preview_path

    reason = (
        "Original-layout PDF conversion requires LibreOffice (`soffice`) to be installed on the server."
        if suffix in OFFICE_PREVIEW_EXTENSIONS and not conversion_error
        else "No PDF preview converter is available for this file type."
    )
    if conversion_error:
        reason = f"Original-layout PDF conversion failed.\n{conversion_error}"
    _write_text_pdf(
        preview_path,
        filename,
        f"{reason}\n\nFile: {filename}\nContent-Type: {media_type}\nSource: {source_path.name}",
    )
    return preview_path
