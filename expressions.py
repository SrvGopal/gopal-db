"""Expression evaluator.

evaluate(expr, row) computes the value of an expression for one row (a dict
of column -> value). It follows SQL's NULL rules (three-valued logic):

    NULL = anything  -> NULL        NULL AND FALSE -> FALSE
    NULL OR TRUE     -> TRUE        NOT NULL       -> NULL

A WHERE clause keeps a row only if the predicate is exactly "true".
"""

import re
from functools import lru_cache

from ast_nodes import Aggregate, BinaryOp, ColumnRef, IsNull, Literal, UnaryOp
from errors import ExecutionError


def is_true(value):
    return value is not None and bool(value)


def _require_number(v):
    if not isinstance(v, (int, float)):
        raise ExecutionError(f"Expected a number but got {v!r}")


@lru_cache(maxsize=256)
def _like_regex(pattern):
    parts = []
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


def evaluate(expr, row):
    if isinstance(expr, Literal):
        return expr.value

    if isinstance(expr, ColumnRef):
        try:
            return row[expr.name]
        except KeyError:
            raise ExecutionError(f"Unknown column '{expr.name}'")

    if isinstance(expr, UnaryOp):
        v = evaluate(expr.operand, row)
        if expr.op == "NOT":
            return None if v is None else not is_true(v)
        if v is None:
            return None
        _require_number(v)
        return -v

    if isinstance(expr, IsNull):
        result = evaluate(expr.operand, row) is None
        return (not result) if expr.negated else result

    if isinstance(expr, BinaryOp):
        return _eval_binary(expr, row)

    if isinstance(expr, Aggregate):
        raise ExecutionError("Aggregate functions are not allowed here")

    raise ExecutionError(f"Cannot evaluate {expr!r}")


def _eval_binary(expr, row):
    op = expr.op

    if op == "AND":
        left = evaluate(expr.left, row)
        if left is not None and not is_true(left):
            return False
        right = evaluate(expr.right, row)
        if right is not None and not is_true(right):
            return False
        return None if (left is None or right is None) else True

    if op == "OR":
        left = evaluate(expr.left, row)
        if left is not None and is_true(left):
            return True
        right = evaluate(expr.right, row)
        if right is not None and is_true(right):
            return True
        return None if (left is None or right is None) else False

    left = evaluate(expr.left, row)
    right = evaluate(expr.right, row)
    if left is None or right is None:
        return None

    if op in ("+", "-", "*", "/"):
        _require_number(left)
        _require_number(right)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        return None if right == 0 else left / right      # MySQL: x/0 is NULL

    if op == "LIKE":
        if not (isinstance(left, str) and isinstance(right, str)):
            raise ExecutionError("LIKE needs text on both sides")
        return bool(_like_regex(right).fullmatch(left))

    try:
        if op == "=":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
    except TypeError:
        raise ExecutionError(
            f"Cannot compare {type(left).__name__} with {type(right).__name__}")

    raise ExecutionError(f"Unknown operator {op}")
