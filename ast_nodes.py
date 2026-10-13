"""AST (Abstract Syntax Tree) node definitions.

The parser turns a list of tokens into these small dataclasses. Everything
after the parser (planner, optimizer, executor) works on these nodes and
never has to look at raw SQL text again.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------
class Expr:
    """Marker base class for all expression nodes."""


@dataclass
class Literal(Expr):
    value: Any


@dataclass
class ColumnRef(Expr):
    name: str


@dataclass
class UnaryOp(Expr):
    op: str            # "NOT" or "-"
    operand: Expr


@dataclass
class BinaryOp(Expr):
    op: str            # AND OR = != < <= > >= LIKE + - * /
    left: Expr
    right: Expr


@dataclass
class IsNull(Expr):
    operand: Expr
    negated: bool = False   # True for IS NOT NULL


@dataclass
class Aggregate(Expr):
    func: str                # COUNT SUM AVG MIN MAX
    arg: Optional[Expr]      # None means COUNT(*)


@dataclass
class Star(Expr):
    pass


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------
@dataclass
class ColumnDef:
    name: str
    type: str                 # INT / FLOAT / TEXT
    primary_key: bool = False


@dataclass
class CreateTable:
    name: str
    columns: List[ColumnDef]


@dataclass
class DropTable:
    name: str


@dataclass
class CreateIndex:
    table: str
    column: str
    name: Optional[str] = None


@dataclass
class Insert:
    table: str
    columns: Optional[List[str]]
    rows: List[List[Expr]]


@dataclass
class Select:
    items: List[Expr]
    table: str
    where: Optional[Expr] = None
    order_by: List[Tuple[Expr, bool]] = field(default_factory=list)  # (expr, is_desc)
    limit: Optional[int] = None


@dataclass
class Update:
    table: str
    assignments: List[Tuple[str, Expr]]
    where: Optional[Expr] = None


@dataclass
class Delete:
    table: str
    where: Optional[Expr] = None


@dataclass
class ShowTables:
    pass


@dataclass
class Describe:
    table: str


@dataclass
class Explain:
    statement: Any


# --------------------------------------------------------------------------
# Helpers used by planner / optimizer
# --------------------------------------------------------------------------
def walk(expr):
    """Yield expr and every sub-expression inside it."""
    yield expr
    if isinstance(expr, BinaryOp):
        yield from walk(expr.left)
        yield from walk(expr.right)
    elif isinstance(expr, (UnaryOp, IsNull)):
        yield from walk(expr.operand)
    elif isinstance(expr, Aggregate) and expr.arg is not None:
        yield from walk(expr.arg)


def columns_in(expr):
    return [n.name for n in walk(expr) if isinstance(n, ColumnRef)]


def contains_aggregate(expr):
    return any(isinstance(n, Aggregate) for n in walk(expr))


def split_conjuncts(expr):
    """a AND b AND c  ->  [a, b, c]"""
    if isinstance(expr, BinaryOp) and expr.op == "AND":
        return split_conjuncts(expr.left) + split_conjuncts(expr.right)
    return [expr]


def join_conjuncts(parts):
    result = parts[0]
    for p in parts[1:]:
        result = BinaryOp("AND", result, p)
    return result


_PREC = {"OR": 1, "AND": 2, "NOT": 3,
         "=": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4, "LIKE": 4,
         "+": 5, "-": 5, "*": 6, "/": 6}


def _prec(e):
    if isinstance(e, BinaryOp):
        return _PREC[e.op]
    if isinstance(e, UnaryOp):
        return 3 if e.op == "NOT" else 7
    if isinstance(e, IsNull):
        return 4
    return 10


def _wrap(child, parent_prec, right=False):
    text = to_sql(child)
    p = _prec(child)
    if p < parent_prec or (right and p == parent_prec and isinstance(child, BinaryOp)):
        return f"({text})"
    return text


def to_sql(e):
    """Turn an expression back into readable SQL (used for column labels and EXPLAIN)."""
    if isinstance(e, Literal):
        v = e.value
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        return str(v)
    if isinstance(e, ColumnRef):
        return e.name
    if isinstance(e, Star):
        return "*"
    if isinstance(e, UnaryOp):
        if e.op == "NOT":
            return f"NOT {_wrap(e.operand, 3)}"
        return f"-{_wrap(e.operand, 7)}"
    if isinstance(e, BinaryOp):
        p = _PREC[e.op]
        return f"{_wrap(e.left, p)} {e.op} {_wrap(e.right, p, right=True)}"
    if isinstance(e, IsNull):
        return f"{_wrap(e.operand, 5)} IS {'NOT ' if e.negated else ''}NULL"
    if isinstance(e, Aggregate):
        return f"{e.func}({'*' if e.arg is None else to_sql(e.arg)})"
    return repr(e)
