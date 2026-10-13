"""Optimizer: rewrites a plan into a cheaper, equivalent plan.

Rules (applied bottom-up):

 1. Constant folding        WHERE age > 10 + 10       ->  WHERE age > 20
 2. Boolean simplification  WHERE x AND TRUE          ->  WHERE x
 3. Dead-filter removal     WHERE 1 = 1               ->  (no filter at all)
                            WHERE 1 = 2               ->  EmptyResult (no scan)
 4. Index selection         Filter(id = 5) over SeqScan ->  IndexScan(id = 5)
                            (leftover AND-ed conditions stay as a Filter)
"""

from dataclasses import replace

import ast_nodes as ast
from ast_nodes import join_conjuncts, split_conjuncts
from errors import DBError
from expressions import evaluate, is_true
from planner import EmptyScan, ExplainPlan, Filter, IndexScan, SeqScan


def fold(expr):
    """Constant folding + boolean simplification."""
    if isinstance(expr, ast.BinaryOp):
        left, right = fold(expr.left), fold(expr.right)
        node = ast.BinaryOp(expr.op, left, right)

        if isinstance(left, ast.Literal) and isinstance(right, ast.Literal):
            try:
                return ast.Literal(evaluate(node, {}))
            except DBError:
                return node            # let the executor report the real error

        if expr.op in ("AND", "OR"):
            for a, b in ((left, right), (right, left)):
                if isinstance(a, ast.Literal) and a.value is not None:
                    truthy = is_true(a.value)
                    if expr.op == "AND":
                        return b if truthy else ast.Literal(False)
                    return ast.Literal(True) if truthy else b
        return node

    if isinstance(expr, ast.UnaryOp):
        operand = fold(expr.operand)
        node = ast.UnaryOp(expr.op, operand)
        if isinstance(operand, ast.Literal):
            try:
                return ast.Literal(evaluate(node, {}))
            except DBError:
                pass
        return node

    if isinstance(expr, ast.IsNull):
        operand = fold(expr.operand)
        node = ast.IsNull(operand, expr.negated)
        if isinstance(operand, ast.Literal):
            return ast.Literal(evaluate(node, {}))
        return node

    if isinstance(expr, ast.Aggregate) and expr.arg is not None:
        return ast.Aggregate(expr.func, fold(expr.arg))

    return expr


class Optimizer:
    def __init__(self, db):
        self.db = db

    def optimize(self, node):
        # 1. optimise children first (bottom-up)
        if hasattr(node, "child"):
            node = replace(node, child=self.optimize(node.child))

        # 2. fold constants inside this node's expressions
        if hasattr(node, "predicate"):
            node = replace(node, predicate=fold(node.predicate))
        if hasattr(node, "items") and isinstance(node.items, list) and node.items \
                and isinstance(node.items[0], tuple):
            node = replace(node, items=[(label, fold(e)) for label, e in node.items])
        if hasattr(node, "keys"):
            node = replace(node, keys=[(fold(e), d) for e, d in node.keys])
        if hasattr(node, "assignments"):
            node = replace(node, assignments=[(c, fold(e)) for c, e in node.assignments])
        if hasattr(node, "rows") and hasattr(node, "columns"):          # InsertPlan
            node = replace(node, rows=[[fold(e) for e in row] for row in node.rows])

        # 3. plan-level rewrites
        if isinstance(node, Filter):
            return self._rewrite_filter(node)
        return node

    # ------------------------------------------------------------------
    def _rewrite_filter(self, node):
        pred = node.predicate

        if isinstance(pred, ast.Literal):
            if is_true(pred.value):
                return node.child                              # filter does nothing
            if isinstance(node.child, SeqScan):
                return EmptyScan(node.child.table)             # filter rejects everything
            return node

        if not isinstance(node.child, SeqScan):
            return node

        table = self.db.get_table(node.child.table)
        conjuncts = split_conjuncts(pred)

        best = None
        for i, c in enumerate(conjuncts):
            hit = self._index_condition(c, table)
            if hit is not None:
                is_pk = hit[0] == table.primary_key
                if best is None or (is_pk and not best[3]):
                    best = (i, hit[0], hit[1], is_pk)
        if best is None:
            return node

        i, column, value, _ = best
        scan = IndexScan(table.name, column, value)
        rest = conjuncts[:i] + conjuncts[i + 1:]
        return Filter(scan, join_conjuncts(rest)) if rest else scan

    @staticmethod
    def _index_condition(expr, table):
        """Match `column = literal` (either order) on an indexed column."""
        if not (isinstance(expr, ast.BinaryOp) and expr.op == "="):
            return None
        for col, lit in ((expr.left, expr.right), (expr.right, expr.left)):
            if (isinstance(col, ast.ColumnRef) and isinstance(lit, ast.Literal)
                    and lit.value is not None and table.has_index(col.name)):
                return col.name, lit.value
        return None
