"""All the custom exceptions in one place.

Every error the database can raise inherits from DBError, so main.py can
catch just one type and print a clean message instead of a Python traceback.
"""


class DBError(ValueError):
    """Base class for every GopalDB error."""


class LexerError(DBError):
    """Bad characters / unterminated strings in the SQL text."""


class ParseError(DBError):
    """The tokens do not form valid SQL."""


class PlanError(DBError):
    """Valid SQL, but it refers to things that don't exist (table, column)."""


class ExecutionError(DBError):
    """Something went wrong while running the query (type mismatch, etc.)."""


class StorageError(DBError):
    """Storage-level violation (duplicate primary key, wrong type, ...)."""
