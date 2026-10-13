"""Glue: SQL text -> lexer -> parser -> planner -> optimizer -> executor."""

from executor import Executor
from lexer import tokenize
from optimizer import Optimizer
from parser import Parser
from planner import Planner


def run(db, sql):
    """Run one or more ';'-separated statements. Returns a list of Results."""
    statements = Parser(tokenize(sql)).parse_script()
    planner, optimizer, executor = Planner(db), Optimizer(db), Executor(db)
    results = []
    for stmt in statements:
        # plan each statement right before running it, so that a CREATE TABLE
        # earlier in the same script is visible to the statements after it
        plan = optimizer.optimize(planner.plan(stmt))
        results.append(executor.execute(plan))
    return results
