"""Local SQLite data layer.

Split (4.1) into ``utils`` (helpers), ``migrations`` (schema/versioning),
``search`` (FTS5 index + queries) and ``database`` (connection + CRUD). The
public surface is unchanged: importers keep using ``Database``, ``utc_now``,
``row_to_dict`` and ``rows_to_dicts`` from ``backend.app.db``.
"""

from __future__ import annotations

from .database import Database
from .utils import row_to_dict, rows_to_dicts, utc_now

__all__ = ["Database", "row_to_dict", "rows_to_dicts", "utc_now"]
