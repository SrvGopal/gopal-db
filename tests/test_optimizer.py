import pytest

from engine import run
from storage import Database


@pytest.fixture
def db():
    d = Database()
    run(d, "CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);"
           "INSERT INTO users VALUES (1,'A',20),(2,'B',30),(3,'C',30);")
    return d


def plan(db, sql):
    return "\n".join(r[0] for r in run(db, "EXPLAIN " + sql)[-1].rows)


def test_primary_key_uses_index(db):
    p = plan(db, "SELECT * FROM users WHERE id = 2;")
    assert "IndexScan" in p and "SeqScan" not in p


def test_non_indexed_column_scans(db):
    assert "SeqScan" in plan(db, "SELECT * FROM users WHERE age = 30;")


def test_create_index_changes_plan_and_keeps_results(db):
    before = run(db, "SELECT name FROM users WHERE age = 30;")[-1].rows
    run(db, "CREATE INDEX ON users (age);")
    assert "IndexScan" in plan(db, "SELECT name FROM users WHERE age = 30;")
    assert run(db, "SELECT name FROM users WHERE age = 30;")[-1].rows == before


def test_residual_filter_after_index(db):
    p = plan(db, "SELECT * FROM users WHERE id = 2 AND age > 10;")
    assert "IndexScan" in p and "Filter: age > 10" in p


def test_constant_folding(db):
    assert "age > 20" in plan(db, "SELECT * FROM users WHERE age > 10 + 10;")


def test_always_true_filter_removed(db):
    p = plan(db, "SELECT * FROM users WHERE 1 = 1;")
    assert "Filter" not in p


def test_always_false_filter_skips_scan(db):
    p = plan(db, "SELECT * FROM users WHERE 1 = 2;")
    assert "EmptyResult" in p and "SeqScan" not in p
    assert run(db, "SELECT * FROM users WHERE 1 = 2;")[-1].rows == []


def test_index_stays_correct_after_update_and_delete(db):
    run(db, "UPDATE users SET id = 10 WHERE id = 1;")
    assert run(db, "SELECT name FROM users WHERE id = 1;")[-1].rows == []
    assert run(db, "SELECT name FROM users WHERE id = 10;")[-1].rows == [("A",)]
    run(db, "DELETE FROM users WHERE id = 10;")
    assert run(db, "SELECT name FROM users WHERE id = 10;")[-1].rows == []
