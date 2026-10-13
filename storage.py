"""Storage engine: typed tables, a primary key, hash indexes and JSON persistence.

Rows live in a dict {row_id: {column: value}}. A hash index maps
value -> set(row_ids), so an equality lookup is O(1) instead of scanning
the whole table.
"""

import json
import os
from dataclasses import dataclass

from errors import StorageError

VALID_TYPES = ("INT", "FLOAT", "TEXT", "ANY")


@dataclass
class Column:
    name: str
    type: str = "ANY"          # "ANY" = no type checking (plain column names still work)
    primary_key: bool = False


class Table:
    def __init__(self, name, columns):
        self.name = name
        self.columns = [c if isinstance(c, Column) else Column(c) for c in columns]

        names = [c.name for c in self.columns]
        if not names:
            raise StorageError("A table needs at least one column")
        if len(set(names)) != len(names):
            raise StorageError(f"Duplicate column name in table {name}")
        for c in self.columns:
            if c.type not in VALID_TYPES:
                raise StorageError(f"Unknown type {c.type} for column {c.name}")
        pks = [c.name for c in self.columns if c.primary_key]
        if len(pks) > 1:
            raise StorageError("Only one PRIMARY KEY column is supported")

        self.primary_key = pks[0] if pks else None
        self._by_name = {c.name: c for c in self.columns}
        self.rows = {}              # row_id -> row dict
        self._next_rid = 1
        self.indexes = {}           # column -> {value: set(row_ids)}
        if self.primary_key:
            self.indexes[self.primary_key] = {}

    # ------------------------------------------------------------- metadata
    @property
    def column_names(self):
        return [c.name for c in self.columns]

    def has_column(self, name):
        return name in self._by_name

    def has_index(self, column):
        return column in self.indexes

    # ---------------------------------------------------------- type checks
    def _coerce(self, col, value):
        if value is None:
            return None
        t = col.type
        if t == "ANY":
            return value
        if t == "INT":
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
        elif t == "FLOAT":
            if isinstance(value, (int, float)):
                return float(value)
        elif t == "TEXT":
            if isinstance(value, str):
                return value
        raise StorageError(f"Incorrect {t} value {value!r} for column '{col.name}'")

    # -------------------------------------------------------------- indexes
    def _index_add(self, rid, row):
        for col, idx in self.indexes.items():
            v = row[col]
            if v is not None:
                idx.setdefault(v, set()).add(rid)

    def _index_remove(self, rid, row):
        for col, idx in self.indexes.items():
            v = row[col]
            if v is not None and v in idx:
                idx[v].discard(rid)
                if not idx[v]:
                    del idx[v]

    def create_index(self, column):
        if column not in self._by_name:
            raise StorageError(f"Unknown column {column} for table {self.name}")
        if column in self.indexes:
            raise StorageError(f"Column '{column}' is already indexed")
        idx = {}
        for rid, row in self.rows.items():
            if row[column] is not None:
                idx.setdefault(row[column], set()).add(rid)
        self.indexes[column] = idx

    # ---------------------------------------------------------- row access
    def insert(self, row):
        for name in row:
            if name not in self._by_name:
                raise StorageError(f"Unknown column {name} for table {self.name}")

        new = {c.name: self._coerce(c, row.get(c.name)) for c in self.columns}

        pk = self.primary_key
        if pk:
            if new[pk] is None:
                raise StorageError(f"Column '{pk}' (PRIMARY KEY) cannot be NULL")
            if new[pk] in self.indexes[pk]:
                raise StorageError(f"Duplicate entry {new[pk]!r} for PRIMARY KEY '{pk}'")

        rid = self._next_rid
        self._next_rid += 1
        self.rows[rid] = new
        self._index_add(rid, new)
        return rid

    def update(self, rid, changes):
        """Apply {column: new_value} to one row. Returns the OLD row (for rollback)."""
        old = self.rows[rid]
        new = dict(old)
        for name, value in changes.items():
            if name not in self._by_name:
                raise StorageError(f"Unknown column {name} for table {self.name}")
            new[name] = self._coerce(self._by_name[name], value)

        pk = self.primary_key
        if pk and pk in changes:
            if new[pk] is None:
                raise StorageError(f"Column '{pk}' (PRIMARY KEY) cannot be NULL")
            if self.indexes[pk].get(new[pk], set()) - {rid}:
                raise StorageError(f"Duplicate entry {new[pk]!r} for PRIMARY KEY '{pk}'")

        self._index_remove(rid, old)
        self.rows[rid] = new
        self._index_add(rid, new)
        return old

    def delete(self, rid):
        row = self.rows.pop(rid)
        self._index_remove(rid, row)

    def scan(self):
        """Full table scan: yields (row_id, row). Works on a snapshot, so it is
        safe to modify the table while iterating."""
        for rid, row in list(self.rows.items()):
            yield rid, row

    def lookup(self, column, value):
        """Index lookup: yields (row_id, row) for rows where column == value."""
        if column not in self.indexes:
            raise StorageError(f"No index on column '{column}'")
        for rid in sorted(self.indexes[column].get(value, ())):
            yield rid, self.rows[rid]

    def get_all_rows(self):
        return list(self.rows.values())

    # --------------------------------------------------------- persistence
    def to_dict(self):
        names = self.column_names
        return {
            "name": self.name,
            "columns": [{"name": c.name, "type": c.type, "primary_key": c.primary_key}
                        for c in self.columns],
            "indexes": [c for c in self.indexes if c != self.primary_key],
            "rows": [[row[n] for n in names] for row in self.rows.values()],
        }

    @classmethod
    def from_dict(cls, data):
        table = cls(data["name"], [Column(**c) for c in data["columns"]])
        names = table.column_names
        for values in data["rows"]:
            table.insert(dict(zip(names, values)))
        for column in data.get("indexes", []):
            table.create_index(column)
        return table


class Database:
    def __init__(self, path=None):
        self.tables = {}
        self.path = path
        if path and os.path.exists(path):
            self.load()

    def create_table(self, table_name, columns):
        if table_name in self.tables:
            raise StorageError(f"Table {table_name} already exists")
        self.tables[table_name] = Table(table_name, columns)

    def get_table(self, table_name):
        if table_name in self.tables:
            return self.tables[table_name]
        raise StorageError(f"Table {table_name} does not exist")

    def has_table(self, table_name):
        return table_name in self.tables

    def delete_table(self, table_name):
        if not self.has_table(table_name):
            raise StorageError(f"Table {table_name} does not exist")
        del self.tables[table_name]

    def save(self):
        """Write everything to disk. Writes to a temp file first, then renames,
        so a crash mid-write can't corrupt the existing database file."""
        if not self.path:
            return
        data = {"version": 1, "tables": [t.to_dict() for t in self.tables.values()]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.tables = {t["name"]: Table.from_dict(t) for t in data["tables"]}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise StorageError(f"Database file {self.path} is corrupt: {e}")
