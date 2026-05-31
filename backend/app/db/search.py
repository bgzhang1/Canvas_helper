from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .utils import utc_now


class SearchMixin:
    """FTS5 search-index construction and querying for ``Database``."""

    def _create_search_index(self, conn: sqlite3.Connection) -> None:
        self._create_search_index_schema(conn)
        self._rebuild_search_index(conn)

    def _create_search_index_schema(self, conn: sqlite3.Connection) -> bool:
        docs_existed = self._table_exists(conn, "course_material_docs")
        fts_existed = self._table_exists(conn, "course_material_docs_fts")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS course_material_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                course_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT,
                indexed_at TEXT NOT NULL,
                UNIQUE(source, source_id),
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_course_material_docs_course_source
            ON course_material_docs(course_id, source, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_course_material_docs_source_id
            ON course_material_docs(source, source_id);
            """
        )
        if self._sqlite_supports_fts5(conn):
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS course_material_docs_fts
                USING fts5(title, body, tokenize='unicode61 remove_diacritics 2')
                """
            )
        return (not docs_existed) or (not fts_existed and self._table_exists(conn, "course_material_docs_fts"))

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return bool(row)

    def _sqlite_supports_fts5(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(value)")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            try:
                conn.execute("DROP TABLE IF EXISTS temp._fts5_probe")
            except sqlite3.OperationalError:
                pass

    def _rebuild_search_index(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "course_material_docs"):
            return
        if self._table_exists(conn, "course_material_docs_fts"):
            conn.execute("DELETE FROM course_material_docs_fts")
        conn.execute("DELETE FROM course_material_docs")

        for doc in self._iter_search_documents(conn):
            self.upsert_search_document(conn, **doc)

    def _iter_search_documents(self, conn: sqlite3.Connection) -> Iterable[dict[str, Any]]:
        for row in conn.execute(
            """
            SELECT id, course_id, title, message, posted_at, author_name
            FROM announcements
            """
        ):
            yield {
                "source": "announcement",
                "source_id": row["id"],
                "course_id": row["course_id"],
                "title": row["title"],
                "body": row["message"] or "",
                "metadata": {"posted_at": row["posted_at"], "author_name": row["author_name"]},
                "updated_at": row["posted_at"],
            }

        for row in conn.execute(
            """
            SELECT id, course_id, name, due_at, unlock_at, lock_at, points_possible, raw_json
            FROM assignments
            """
        ):
            yield {
                "source": "assignment",
                "source_id": row["id"],
                "course_id": row["course_id"],
                "title": row["name"],
                "body": self.assignment_search_body(dict(row)),
                "metadata": {
                    "due_at": row["due_at"],
                    "unlock_at": row["unlock_at"],
                    "lock_at": row["lock_at"],
                    "points_possible": row["points_possible"],
                },
                "updated_at": row["due_at"] or row["unlock_at"] or row["lock_at"],
            }

        for row in conn.execute(
            """
            SELECT course_id, page_url, title, body, updated_at
            FROM pages
            """
        ):
            yield {
                "source": "page",
                "source_id": f"{row['course_id']}:{row['page_url']}",
                "course_id": row["course_id"],
                "title": row["title"],
                "body": row["body"] or "",
                "metadata": {"page_url": row["page_url"], "updated_at": row["updated_at"]},
                "updated_at": row["updated_at"],
            }

        for row in conn.execute(
            """
            SELECT id, course_id, display_name, content_type, updated_at,
                   extraction_status, outline_json, extracted_text_path
            FROM files
            """
        ):
            yield {
                "source": "file",
                "source_id": row["id"],
                "course_id": row["course_id"],
                "title": row["display_name"],
                "body": self.file_search_body(dict(row)),
                "metadata": {
                    "content_type": row["content_type"],
                    "updated_at": row["updated_at"],
                    "extraction_status": row["extraction_status"],
                },
                "updated_at": row["updated_at"],
            }

    def assignment_search_body(self, row: dict[str, Any]) -> str:
        parts = [
            str(row.get("name") or ""),
            f"due_at: {row.get('due_at') or ''}",
            f"unlock_at: {row.get('unlock_at') or ''}",
            f"lock_at: {row.get('lock_at') or ''}",
            f"points_possible: {row.get('points_possible') or ''}",
        ]
        raw_json = row.get("raw_json")
        if raw_json:
            try:
                data = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                data = {}
            if isinstance(data, dict) and isinstance(data.get("description"), str):
                parts.append(self._html_to_text(data["description"]))
        return "\n".join(part for part in parts if part)

    def file_search_body(self, row: dict[str, Any], extracted_text: str | None = None) -> str:
        parts: list[str] = []
        outline_json = row.get("outline_json")
        if outline_json:
            try:
                outline = json.loads(outline_json)
                if isinstance(outline, list):
                    for item in outline:
                        if isinstance(item, dict) and item.get("title"):
                            parts.append(str(item["title"]))
                        elif item:
                            parts.append(str(item))
                else:
                    parts.append(str(outline))
            except (json.JSONDecodeError, TypeError):
                parts.append(str(outline_json))
        if extracted_text is not None:
            parts.append(extracted_text)
        else:
            path_value = row.get("extracted_text_path")
            if path_value:
                path = Path(path_value)
                if path.exists():
                    try:
                        parts.append(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
        return "\n".join(part for part in parts if part)

    def upsert_search_document(
        self,
        conn: sqlite3.Connection,
        *,
        source: str,
        source_id: str | int,
        course_id: int,
        title: str,
        body: str | None = "",
        metadata: dict[str, Any] | None = None,
        updated_at: str | None = None,
        preserve_existing_body: bool = False,
    ) -> None:
        if not self._table_exists(conn, "course_material_docs"):
            return
        source_id_value = str(source_id)
        body_value = body or ""
        if preserve_existing_body and not body_value:
            row = conn.execute(
                """
                SELECT body
                FROM course_material_docs
                WHERE source = ? AND source_id = ?
                """,
                (source, source_id_value),
            ).fetchone()
            if row and row["body"]:
                body_value = row["body"]
        aliases = self._search_aliases(title)
        if aliases:
            missing_aliases = [alias for alias in aliases if alias.lower() not in body_value.lower()]
            if missing_aliases:
                body_value = "\n".join(part for part in [body_value, " ".join(missing_aliases)] if part)
        now = utc_now()
        conn.execute(
            """
            INSERT INTO course_material_docs(
                source, source_id, course_id, title, body, metadata_json, updated_at, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                course_id=excluded.course_id,
                title=excluded.title,
                body=excluded.body,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at,
                indexed_at=excluded.indexed_at
            """,
            (
                source,
                source_id_value,
                course_id,
                title or "Untitled",
                body_value,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                updated_at,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT id, title, body
            FROM course_material_docs
            WHERE source = ? AND source_id = ?
            """,
            (source, source_id_value),
        ).fetchone()
        if row:
            self._sync_search_fts_row(conn, int(row["id"]), row["title"], row["body"])

    def delete_search_document(
        self,
        conn: sqlite3.Connection,
        *,
        source: str,
        source_id: str | int,
    ) -> None:
        if not self._table_exists(conn, "course_material_docs"):
            return
        source_id_value = str(source_id)
        row = conn.execute(
            """
            SELECT id
            FROM course_material_docs
            WHERE source = ? AND source_id = ?
            """,
            (source, source_id_value),
        ).fetchone()
        if row and self._table_exists(conn, "course_material_docs_fts"):
            conn.execute("DELETE FROM course_material_docs_fts WHERE rowid = ?", (row["id"],))
        conn.execute(
            """
            DELETE FROM course_material_docs
            WHERE source = ? AND source_id = ?
            """,
            (source, source_id_value),
        )

    def _sync_search_fts_row(self, conn: sqlite3.Connection, row_id: int, title: str, body: str) -> None:
        if not self._table_exists(conn, "course_material_docs_fts"):
            return
        conn.execute("DELETE FROM course_material_docs_fts WHERE rowid = ?", (row_id,))
        conn.execute(
            """
            INSERT INTO course_material_docs_fts(rowid, title, body)
            VALUES (?, ?, ?)
            """,
            (row_id, title or "", body or ""),
        )

    def rebuild_search_index(self) -> None:
        with self.connect() as conn:
            if self._create_search_index_schema(conn):
                self._rebuild_search_index(conn)
            else:
                self._rebuild_search_index(conn)

    def search_course_materials(
        self,
        query: str,
        *,
        course: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]] | None:
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []
        capped_limit = max(1, min(limit, 20))
        course_filter = course.strip().lower()
        with self.connect() as conn:
            if not self._table_exists(conn, "course_material_docs_fts"):
                return None
            try:
                rows = self._execute_course_material_search(conn, fts_query, course_filter, capped_limit)
                relaxed_query = self._build_relaxed_fts_query(query)
                if relaxed_query and (not rows or len(self._fts_term_groups(query, drop_stopwords=True)) >= 6):
                    relaxed_rows = self._execute_course_material_search(
                        conn,
                        relaxed_query,
                        course_filter,
                        max(100, capped_limit * 10),
                    )
                    if relaxed_rows:
                        rows = self._rerank_relaxed_search_rows(relaxed_rows, query)[:capped_limit]
            except sqlite3.OperationalError:
                return None

        results = []
        for row in rows:
            snippet = row["snippet"] or self._plain_snippet(row["body"] or row["title"], query)
            results.append(
                {
                    "course": row["course"],
                    "source": row["source"],
                    "title": row["title"],
                    "snippet": snippet.strip(),
                }
            )
        return results

    def _execute_course_material_search(
        self,
        conn: sqlite3.Connection,
        fts_query: str,
        course_filter: str,
        limit: int,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT
                d.id,
                d.source,
                d.title,
                d.body,
                COALESCE(NULLIF(c.course_code, ''), NULLIF(c.name, ''), 'course_' || d.course_id) AS course,
                snippet(course_material_docs_fts, -1, '', '', '...', 64) AS snippet,
                bm25(course_material_docs_fts) AS rank
            FROM course_material_docs_fts
            JOIN course_material_docs d ON d.id = course_material_docs_fts.rowid
            JOIN courses c ON c.id = d.course_id
            WHERE course_material_docs_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if course_filter:
            sql += """
                AND lower(COALESCE(c.course_code, '') || ' ' || COALESCE(c.name, '')) LIKE ?
            """
            params.append(f"%{course_filter}%")
        sql += """
            ORDER BY rank, d.updated_at DESC, d.title
            LIMIT ?
        """
        params.append(limit)
        return conn.execute(sql, params).fetchall()

    def _build_fts_query(self, query: str) -> str:
        groups = self._fts_term_groups(query)
        if not groups:
            return ""
        return " AND ".join(self._fts_group_query(group) for group in groups)

    def _build_relaxed_fts_query(self, query: str) -> str:
        groups = self._fts_term_groups(query, drop_stopwords=True)
        atoms = sorted({atom for group in groups for atom in group})
        if len(atoms) < 2:
            return ""
        return " OR ".join(f'"{self._escape_fts_term(atom)}"*' for atom in atoms[:32])

    def _fts_term_groups(self, query: str, *, drop_stopwords: bool = False) -> list[list[str]]:
        terms = [term.lower() for term in re.findall(r"\w+", query, flags=re.UNICODE)[:12]]
        stopwords = {
            "a",
            "an",
            "and",
            "after",
            "all",
            "at",
            "before",
            "by",
            "during",
            "find",
            "for",
            "from",
            "in",
            "of",
            "on",
            "or",
            "please",
            "room",
            "that",
            "the",
            "this",
            "to",
            "usual",
            "with",
        }
        groups = []
        for index, term in enumerate(terms):
            if drop_stopwords and (len(term) <= 1 or term in stopwords):
                continue
            alts = {term}
            compact_alts = set()
            if index > 0:
                compact = self._compact_fts_pair(terms[index - 1], term)
                if compact:
                    compact_alts.add(compact)
            if index + 1 < len(terms):
                compact = self._compact_fts_pair(term, terms[index + 1])
                if compact:
                    compact_alts.add(compact)
            if term.isdigit() and compact_alts:
                alts = compact_alts
            else:
                alts.update(compact_alts)
            groups.append(sorted(alts))
        return groups

    def _fts_group_query(self, group: list[str]) -> str:
        return "(" + " OR ".join(f'"{self._escape_fts_term(alt)}"*' for alt in group) + ")"

    def _rerank_relaxed_search_rows(self, rows: list[sqlite3.Row], query: str) -> list[sqlite3.Row]:
        groups = self._fts_term_groups(query, drop_stopwords=True)

        def score(row: sqlite3.Row) -> tuple[int, int, float]:
            title = str(row["title"] or "").lower()
            body = str(row["body"] or "").lower()
            matched = 0
            weighted = 0
            for group in groups:
                if any(alt in title for alt in group):
                    matched += 1
                    weighted += 3
                elif any(alt in body for alt in group):
                    matched += 1
                    weighted += 1
            rank = row["rank"] if isinstance(row["rank"], (int, float)) else 0.0
            return matched, weighted, -float(rank)

        return sorted(rows, key=score, reverse=True)

    def _compact_fts_pair(self, left: str, right: str) -> str:
        left_value = left.lower()
        right_value = right.lower()
        if (
            (left_value.isalpha() and right_value.isdigit())
            or (left_value.isdigit() and right_value.isalpha())
            or (left_value.isalpha() and right_value.isalpha())
        ):
            return left_value + right_value
        return ""

    def _escape_fts_term(self, value: str) -> str:
        return value.replace('"', '""')

    def _search_aliases(self, title: str) -> list[str]:
        aliases: set[str] = set()
        text = title or ""
        for word, number in re.findall(r"([A-Za-z]+)\s+(\d+)", text):
            aliases.add(f"{word}{number}")
            aliases.add(f"{word} {number}")
        for word, number in re.findall(r"([A-Za-z]+)(\d+)", text):
            if word and number:
                aliases.add(f"{word}{number}")
                aliases.add(f"{word} {number}")

        terms = re.findall(r"\w+", text, flags=re.UNICODE)
        for index in range(len(terms) - 1):
            compact = self._compact_fts_pair(terms[index], terms[index + 1])
            if compact:
                aliases.add(compact)

        lowered_terms = {term.lower() for term in terms}
        known_splits = {
            "midterm": "mid term",
            "shortquiz": "short quiz",
            "mockquiz": "mock quiz",
            "popupquiz": "popup quiz",
        }
        for token, split in known_splits.items():
            if token in lowered_terms:
                aliases.add(split)
        return sorted(aliases)

    def _html_to_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()

    def _plain_snippet(self, text: str, query: str, limit: int = 600) -> str:
        lowered = text.lower()
        needle = query.lower()
        idx = lowered.find(needle) if needle else -1
        if idx < 0:
            idx = 0
        start = max(0, idx - 200)
        return text[start : start + limit]
