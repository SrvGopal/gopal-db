"""Lexer (tokenizer): raw SQL text  ->  list of Tokens.

    "SELECT name FROM users WHERE age >= 18;"
        -> KEYWORD(SELECT) IDENT(name) KEYWORD(FROM) IDENT(users) KEYWORD(WHERE)
           IDENT(age) OP(>=) NUMBER(18) PUNCT(;) EOF
"""

import re
from dataclasses import dataclass

from errors import LexerError

KEYWORDS = {
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "CREATE", "TABLE",
    "DROP", "UPDATE", "SET", "DELETE", "AND", "OR", "NOT", "NULL", "IS", "LIKE",
    "ORDER", "BY", "ASC", "DESC", "LIMIT", "PRIMARY", "KEY", "INT", "INTEGER",
    "FLOAT", "TEXT", "VARCHAR", "SHOW", "TABLES", "DESCRIBE", "EXPLAIN",
    "INDEX", "ON", "COUNT", "SUM", "AVG", "MIN", "MAX", "TRUE", "FALSE",
}

TWO_CHAR_OPS = {"<=", ">=", "!=", "<>"}
ONE_CHAR_OPS = set("=<>+-*/")
PUNCTUATION = set("(),;")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Token:
    type: str       # KEYWORD, IDENT, NUMBER, STRING, OP, PUNCT, EOF
    value: object
    pos: int        # character offset in the SQL text (for error messages)


def _read_string(sql, start):
    quote = sql[start]
    i, buf = start + 1, []
    while i < len(sql):
        c = sql[i]
        if c == quote:
            if i + 1 < len(sql) and sql[i + 1] == quote:   # '' -> a literal quote
                buf.append(quote)
                i += 2
                continue
            return i + 1, "".join(buf)
        buf.append(c)
        i += 1
    raise LexerError(f"Unterminated string starting at position {start}")


def tokenize(sql):
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]

        if ch.isspace():
            i += 1
            continue

        if sql.startswith("--", i):                      # comment till end of line
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue

        if ch in ("'", '"'):
            start = i
            i, value = _read_string(sql, i)
            tokens.append(Token("STRING", value, start))
            continue

        m = _NUMBER.match(sql, i)
        if m:
            text = m.group()
            end = m.end()
            if end < n and (sql[end].isalpha() or sql[end] == "_"):
                raise LexerError(f"Invalid number or identifier at position {i}")
            tokens.append(Token("NUMBER", float(text) if "." in text else int(text), i))
            i = end
            continue

        m = _IDENT.match(sql, i)
        if m:
            word = m.group()
            if word.upper() in KEYWORDS:
                tokens.append(Token("KEYWORD", word.upper(), i))
            else:
                tokens.append(Token("IDENT", word, i))
            i = m.end()
            continue

        if sql[i:i + 2] in TWO_CHAR_OPS:
            op = sql[i:i + 2]
            tokens.append(Token("OP", "!=" if op == "<>" else op, i))
            i += 2
            continue

        if ch in ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, i))
            i += 1
            continue

        if ch in PUNCTUATION:
            tokens.append(Token("PUNCT", ch, i))
            i += 1
            continue

        raise LexerError(f"Unexpected character {ch!r} at position {i}")

    tokens.append(Token("EOF", None, n))
    return tokens
