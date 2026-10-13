"""Executor: runs an (optimized) plan against the storage engine.

Uses the classic "iterator" model: every operator is a generator that pulls
rows from its child, so rows stream through the pipeline one at a time.
Scans / filters yield (row_id, row_dict); Project and Aggregate yield
(None, tuple_of_values).
"""

import itertools
from dataclasses import dataclass, field

import ast_nodes as ast
from errors import DBError, ExecutionError
from expressions import evaluate, is_true
from planner import (
    AggregatePlan, DeletePlan, EmptyScan, ExplainPlan, Filter, IndexScan,
    InsertPlan, Limit, Project, SeqScan, Sort, UpdatePlan, UtilityPlan,
    render_plan,
)
from storage import Column


@dataclass
class Result:
    columns: list = field(default_factory=list)   # non-empty for queries
    rows: list = field(default_factory=list)
    message: str = ""
    affected: int = 0
    mutated: bool = False                         # True -> main.py saves to disk


def _sort_key(value):
    return (value is not None, value)             # NULLs sort first


class Executor:
    def __init__(self, db):
        self.db = db

    def execute(self, plan):
        if isinstance(plan, InsertPlan):
            return self._insert(plan)
        if isinstance(plan, UpdatePlan):
            return self._update(plan)
        if isinstance(plan, DeletePlan):
            return self._delete(plan)
        if isinstance(plan, UtilityPlan):
            return self._utility(plan.statement)
        if isinstance(plan, ExplainPlan):
            lines = render_plan(plan.child)
            return Result(columns=["QUERY PLAN"], rows=[(l,) for l in lines])
        return self._query(plan)

    # -------------------------------------------------------------- queries
    def _query(self, plan):
        columns = self._output_columns(plan)
        rows = [row for _, row in self._iter(plan)]
        return Result(columns=columns, rows=rows)

    @staticmethod
    def _output_columns(node):
        while isinstance(node, Limit):
            node = node.child
        if isinstance(node, (Project, AggregatePlan)):
            return [label for label, _ in node.items]
        raise ExecutionError("Internal error: plan has no output columns")

    def _iter(self, node):
        if isinstance(node, SeqScan):
            return self.db.get_table(node.table).scan()
        if isinstance(node, IndexScan):
            return self.db.get_table(node.table).lookup(node.column, node.value)
        if isinstance(node, EmptyScan):
            return iter(())
        if isinstance(node, Filter):
            return self._filter(node)
        if isinstance(node, Sort):
            return self._sort(node)
        if isinstance(node, Project):
            return self._project(node)
        if isinstance(node, AggregatePlan):
            return self._aggregate(node)
        if isinstance(node, Limit):
            return itertools.islice(self._iter(node.child), node.count)
        raise ExecutionError(f"Cannot execute {type(node).__name__}")

    def _filter(self, node):
        for rid, row in self._iter(node.child):
            if is_true(evaluate(node.predicate, row)):
                yield rid, row

    def _sort(self, node):
        rows = list(self._iter(node.child))
        for expr, desc in reversed(node.keys):         # stable sort, last key first
            rows.sort(key=lambda p, e=expr: _sort_key(evaluate(e, p[1])), reverse=desc)
        yield from rows

    def _project(self, node):
        for _, row in self._iter(node.child):
            yield None, tuple(evaluate(expr, row) for _, expr in node.items)

    def _aggregate(self, node):
        rows = [row for _, row in self._iter(node.child)]
        yield None, tuple(self._compute_aggregate(agg, rows) for _, agg in node.items)

    @staticmethod
    def _compute_aggregate(agg, rows):
        if agg.arg is None:                            # COUNT(*)
            return len(rows)
        values = [v for v in (evaluate(agg.arg, r) for r in rows) if v is not None]
        if agg.func == "COUNT":
            return len(values)
        if not values:
            return None
        try:
            if agg.func == "SUM":
                return sum(values)
            if agg.func == "AVG":
                return sum(values) / len(values)
            if agg.func == "MIN":
                return min(values)
            if agg.func == "MAX":
                return max(values)
        except TypeError:
            raise ExecutionError(f"{agg.func}() cannot be used on these values")
        raise ExecutionError(f"Unknown aggregate {agg.func}")

    # ------------------------------------------------------------------ DML
    def _insert(self, plan):
        table = self.db.get_table(plan.table)
        columns = plan.columns or table.column_names
        inserted = []
        try:
            for exprs in plan.rows:
                values = [evaluate(e, {}) for e in exprs]
                inserted.append(table.insert(dict(zip(columns, values))))
        except DBError:
            for rid in inserted:                       # statement is all-or-nothing
                table.delete(rid)
            raise
        n = len(inserted)
        return Result(message=f"Query OK, {n} row(s) affected", affected=n, mutated=True)

    def _update(self, plan):
        table = self.db.get_table(plan.table)
        matches = list(self._iter(plan.child))
        undo = []
        try:
            for rid, row in matches:
                changes = {col: evaluate(expr, row) for col, expr in plan.assignments}
                undo.append((rid, table.update(rid, changes)))
        except DBError:
            for rid, old in reversed(undo):            # roll back partial update
                table.update(rid, old)
            raise
        n = len(undo)
        return Result(message=f"Query OK, {n} row(s) affected", affected=n, mutated=True)

    def _delete(self, plan):
        table = self.db.get_table(plan.table)
        matches = list(self._iter(plan.child))
        for rid, _ in matches:
            table.delete(rid)
        n = len(matches)
        return Result(message=f"Query OK, {n} row(s) affected", affected=n, mutated=True)

    # -------------------------------------------------------- DDL / utility
    def _utility(self, stmt):
        if isinstance(stmt, ast.CreateTable):
            cols = [Column(c.name, c.type, c.primary_key) for c in stmt.columns]
            self.db.create_table(stmt.name, cols)
            return Result(message="Query OK, 0 rows affected", mutated=True)
        if isinstance(stmt, ast.DropTable):
            self.db.delete_table(stmt.name)
            return Result(message="Query OK, 0 rows affected", mutated=True)
        if isinstance(stmt, ast.CreateIndex):
            self.db.get_table(stmt.table).create_index(stmt.column)
            return Result(message="Query OK, 0 rows affected", mutated=True)
        if isinstance(stmt, ast.ShowTables):
            return Result(columns=["Tables"], rows=[(n,) for n in sorted(self.db.tables)])
        if isinstance(stmt, ast.Describe):
            t = self.db.get_table(stmt.table)
            rows = []
            for c in t.columns:
                key = "PRI" if c.primary_key else ("MUL" if t.has_index(c.name) else "")
                rows.append((c.name, c.type, key))
            return Result(columns=["Field", "Type", "Key"], rows=rows)
        raise ExecutionError(f"Cannot execute {type(stmt).__name__}")
