"""Planner: statement AST -> logical plan tree.

A plan is a tree of operators, e.g. for
    SELECT name FROM users WHERE age > 20 ORDER BY name LIMIT 5;

    Limit: 5
    └─ Project: name
       └─ Sort: name
          └─ Filter: age > 20
             └─ SeqScan on users

The planner also does semantic checks: does the table exist? do the columns?
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import ast_nodes as ast
from ast_nodes import columns_in, contains_aggregate, to_sql
from errors import PlanError


# --------------------------------------------------------------------------
# Plan nodes
# --------------------------------------------------------------------------
class PlanNode:
    def children(self):
        return []

    def describe(self):
        return type(self).__name__


@dataclass
class SeqScan(PlanNode):
    table: str

    def describe(self):
        return f"SeqScan on {self.table}  (full table scan)"


@dataclass
class IndexScan(PlanNode):
    table: str
    column: str
    value: Any

    def describe(self):
        return (f"IndexScan on {self.table} using index({self.column}) "
                f"= {to_sql(ast.Literal(self.value))}")


@dataclass
class EmptyScan(PlanNode):
    table: str

    def describe(self):
        return f"EmptyResult on {self.table}  (condition is never true)"


@dataclass
class Filter(PlanNode):
    child: PlanNode
    predicate: ast.Expr

    def children(self):
        return [self.child]

    def describe(self):
        return f"Filter: {to_sql(self.predicate)}"


@dataclass
class Sort(PlanNode):
    child: PlanNode
    keys: List[Tuple[ast.Expr, bool]]

    def children(self):
        return [self.child]

    def describe(self):
        keys = ", ".join(to_sql(e) + (" DESC" if d else "") for e, d in self.keys)
        return f"Sort: {keys}"


@dataclass
class Project(PlanNode):
    child: PlanNode
    items: List[Tuple[str, ast.Expr]]       # (label, expression)

    def children(self):
        return [self.child]

    def describe(self):
        return "Project: " + ", ".join(label for label, _ in self.items)


@dataclass
class AggregatePlan(PlanNode):
    child: PlanNode
    items: List[Tuple[str, ast.Aggregate]]

    def children(self):
        return [self.child]

    def describe(self):
        return "Aggregate: " + ", ".join(label for label, _ in self.items)


@dataclass
class Limit(PlanNode):
    child: PlanNode
    count: int

    def children(self):
        return [self.child]

    def describe(self):
        return f"Limit: {self.count}"


@dataclass
class InsertPlan(PlanNode):
    table: str
    columns: Optional[List[str]]
    rows: List[List[ast.Expr]]

    def describe(self):
        return f"Insert into {self.table}  ({len(self.rows)} row(s))"


@dataclass
class UpdatePlan(PlanNode):
    table: str
    assignments: List[Tuple[str, ast.Expr]]
    child: PlanNode

    def children(self):
        return [self.child]

    def describe(self):
        sets = ", ".join(f"{c} = {to_sql(e)}" for c, e in self.assignments)
        return f"Update {self.table} SET {sets}"


@dataclass
class DeletePlan(PlanNode):
    table: str
    child: PlanNode

    def children(self):
        return [self.child]

    def describe(self):
        return f"Delete from {self.table}"


@dataclass
class UtilityPlan(PlanNode):
    """CREATE TABLE, DROP TABLE, CREATE INDEX, SHOW TABLES, DESCRIBE."""
    statement: Any

    def describe(self):
        return type(self.statement).__name__


@dataclass
class ExplainPlan(PlanNode):
    child: PlanNode

    def children(self):
        return [self.child]

    def describe(self):
        return "Explain"


def render_plan(node):
    """Draw a plan tree as text lines (used by EXPLAIN)."""
    lines = [node.describe()]

    def walk(n, prefix):
        kids = n.children()
        for i, kid in enumerate(kids):
            last = i == len(kids) - 1
            lines.append(prefix + ("└─ " if last else "├─ ") + kid.describe())
            walk(kid, prefix + ("   " if last else "│  "))

    walk(node, "")
    return lines


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------
class Planner:
    def __init__(self, db):
        self.db = db

    def plan(self, stmt):
        if isinstance(stmt, ast.Select):
            return self._plan_select(stmt)
        if isinstance(stmt, ast.Insert):
            return self._plan_insert(stmt)
        if isinstance(stmt, ast.Update):
            return self._plan_update(stmt)
        if isinstance(stmt, ast.Delete):
            return self._plan_delete(stmt)
        if isinstance(stmt, ast.Explain):
            return ExplainPlan(self.plan(stmt.statement))
        if isinstance(stmt, ast.CreateIndex):
            table = self._table(stmt.table)
            if not table.has_column(stmt.column):
                raise PlanError(f"Unknown column '{stmt.column}' in table {stmt.table}")
            return UtilityPlan(stmt)
        if isinstance(stmt, ast.CreateTable):
            if self.db.has_table(stmt.name):
                raise PlanError(f"Table {stmt.name} already exists")
            return UtilityPlan(stmt)
        if isinstance(stmt, (ast.DropTable, ast.Describe)):
            self._table(stmt.name if isinstance(stmt, ast.DropTable) else stmt.table)
            return UtilityPlan(stmt)
        if isinstance(stmt, ast.ShowTables):
            return UtilityPlan(stmt)
        raise PlanError(f"Cannot plan {type(stmt).__name__}")

    # -------------------------------------------------------------- helpers
    def _table(self, name):
        if not self.db.has_table(name):
            raise PlanError(f"Table {name} does not exist")
        return self.db.get_table(name)

    @staticmethod
    def _check(expr, table, clause):
        for name in columns_in(expr):
            if not table.has_column(name):
                raise PlanError(f"Unknown column '{name}' in {clause}")

    def _scan_with_where(self, table, where):
        node = SeqScan(table.name)
        if where is not None:
            if contains_aggregate(where):
                raise PlanError("Aggregate functions are not allowed in WHERE")
            self._check(where, table, "WHERE clause")
            node = Filter(node, where)
        return node

    # --------------------------------------------------------------- SELECT
    def _plan_select(self, stmt):
        table = self._table(stmt.table)

        items = []                                   # (label, expr)
        for item in stmt.items:
            if isinstance(item, ast.Star):
                items.extend((c, ast.ColumnRef(c)) for c in table.column_names)
            else:
                self._check(item, table, "select list")
                items.append((to_sql(item), item))

        node = self._scan_with_where(table, stmt.where)

        has_agg = [contains_aggregate(e) for _, e in items]
        if any(has_agg):
            if not all(isinstance(e, ast.Aggregate) for _, e in items):
                raise PlanError(
                    "Cannot mix aggregates with plain columns or expressions "
                    "(GROUP BY is not supported yet)")
            node = AggregatePlan(node, items)
        else:
            if stmt.order_by:
                for expr, _ in stmt.order_by:
                    if contains_aggregate(expr):
                        raise PlanError("Aggregate functions are not allowed in ORDER BY")
                    self._check(expr, table, "ORDER BY clause")
                node = Sort(node, stmt.order_by)
            node = Project(node, items)

        if stmt.limit is not None:
            node = Limit(node, stmt.limit)
        return node

    # --------------------------------------------------------------- INSERT
    def _plan_insert(self, stmt):
        table = self._table(stmt.table)
        columns = stmt.columns
        if columns is not None:
            for c in columns:
                if not table.has_column(c):
                    raise PlanError(f"Unknown column '{c}' in table {stmt.table}")
            if len(set(columns)) != len(columns):
                raise PlanError("Column listed more than once in INSERT")
        expected = len(columns) if columns is not None else len(table.columns)
        for row in stmt.rows:
            if len(row) != expected:
                raise PlanError(f"Expected {expected} values but got {len(row)}")
            for expr in row:
                if columns_in(expr) or contains_aggregate(expr):
                    raise PlanError("INSERT values must be constants")
        return InsertPlan(stmt.table, columns, stmt.rows)

    # --------------------------------------------------------- UPDATE/DELETE
    def _plan_update(self, stmt):
        table = self._table(stmt.table)
        for col, expr in stmt.assignments:
            if not table.has_column(col):
                raise PlanError(f"Unknown column '{col}' in table {stmt.table}")
            if contains_aggregate(expr):
                raise PlanError("Aggregate functions are not allowed in SET")
            self._check(expr, table, "SET clause")
        return UpdatePlan(stmt.table, stmt.assignments,
                          self._scan_with_where(table, stmt.where))

    def _plan_delete(self, stmt):
        table = self._table(stmt.table)
        return DeletePlan(stmt.table, self._scan_with_where(table, stmt.where))
