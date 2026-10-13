import pytest

from engine import run
from errors import DBError, ExecutionError, PlanError, StorageError
from storage import Database


@pytest.fixture
def db():
    d = Database()
    run(d, """
        CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);
        INSERT INTO users VALUES (1,'Asha',25), (2,'Ravi',31), (3,'Meera',25), (4,'Kabir',40);
    """)
    return d


def q(db, sql):
    return run(db, sql)[-1]


def names(result):
    return [r[0] for r in result.rows]


def test_select_star(db):
    r = q(db, "SELECT * FROM users;")
    assert r.columns == ["id", "name", "age"]
    assert len(r.rows) == 4


def test_where_and_or_not(db):
    assert names(q(db, "SELECT name FROM users WHERE age = 25 AND id > 1;")) == ["Meera"]
    assert names(q(db, "SELECT name FROM users WHERE id = 1 OR id = 4;")) == ["Asha", "Kabir"]
    assert names(q(db, "SELECT name FROM users WHERE NOT age = 25;")) == ["Ravi", "Kabir"]


def test_order_limit(db):
    r = q(db, "SELECT name FROM users ORDER BY age DESC, name ASC LIMIT 2;")
    assert names(r) == ["Kabir", "Ravi"]


def test_expressions(db):
    r = q(db, "SELECT age * 2 + 1 FROM users WHERE id = 1;")
    assert r.rows == [(51,)]


def test_aggregates(db):
    r = q(db, "SELECT COUNT(*), SUM(age), AVG(age), MIN(age), MAX(age) FROM users;")
    assert r.rows == [(4, 121, 30.25, 25, 40)]


def test_aggregate_on_empty_result(db):
    r = q(db, "SELECT COUNT(*), SUM(age) FROM users WHERE id > 100;")
    assert r.rows == [(0, None)]


def test_update_with_expression(db):
    r = q(db, "UPDATE users SET age = age + 1 WHERE age = 25;")
    assert r.affected == 2
    assert names(q(db, "SELECT name FROM users WHERE age = 26;")) == ["Asha", "Meera"]


def test_delete(db):
    assert q(db, "DELETE FROM users WHERE age > 30;").affected == 2
    assert len(q(db, "SELECT * FROM users;").rows) == 2


def test_like(db):
    assert names(q(db, "SELECT name FROM users WHERE name LIKE '%a';")) == ["Asha", "Meera"]
    assert names(q(db, "SELECT name FROM users WHERE name LIKE 'k_bir';")) == ["Kabir"]


def test_null_semantics(db):
    q(db, "INSERT INTO users (id, name) VALUES (5, 'Nina');")
    assert names(q(db, "SELECT name FROM users WHERE age IS NULL;")) == ["Nina"]
    assert q(db, "SELECT name FROM users WHERE age = NULL;").rows == []      # NULL = NULL is not true
    assert len(q(db, "SELECT name FROM users WHERE age IS NOT NULL;").rows) == 4
    assert q(db, "SELECT COUNT(age), COUNT(*) FROM users;").rows == [(4, 5)]


def test_primary_key_duplicate(db):
    with pytest.raises(StorageError):
        q(db, "INSERT INTO users VALUES (1, 'Dup', 1);")


def test_multi_row_insert_is_atomic(db):
    with pytest.raises(StorageError):
        q(db, "INSERT INTO users VALUES (10,'X',1), (1,'Dup',2);")
    assert len(q(db, "SELECT * FROM users;").rows) == 4          # (10,'X',1) rolled back
    assert q(db, "SELECT * FROM users WHERE id = 10;").rows == []


def test_update_rollback_on_failure(db):
    with pytest.raises(StorageError):
        q(db, "UPDATE users SET id = 1;")          # every row would get id = 1
    assert names(q(db, "SELECT name FROM users ORDER BY id;")) == ["Asha", "Ravi", "Meera", "Kabir"]


def test_type_checking(db):
    with pytest.raises(StorageError):
        q(db, "INSERT INTO users VALUES (9, 'X', 'old');")
    with pytest.raises(ExecutionError):
        q(db, "SELECT * FROM users WHERE name > 5;")


def test_semantic_errors(db):
    with pytest.raises(PlanError):
        q(db, "SELECT nope FROM users;")
    with pytest.raises(PlanError):
        q(db, "SELECT * FROM ghosts;")
    with pytest.raises(PlanError):
        q(db, "INSERT INTO users VALUES (1, 2);")
    with pytest.raises(PlanError):
        q(db, "SELECT name, COUNT(*) FROM users;")


def test_show_describe_drop(db):
    assert q(db, "SHOW TABLES;").rows == [("users",)]
    assert ("id", "INT", "PRI") in q(db, "DESCRIBE users;").rows
    q(db, "DROP TABLE users;")
    assert q(db, "SHOW TABLES;").rows == []


def test_script_sees_earlier_statements():
    d = Database()
    r = run(d, "CREATE TABLE t (a INT); INSERT INTO t VALUES (1); SELECT * FROM t;")
    assert r[-1].rows == [(1,)]


def test_persistence(tmp_path):
    path = str(tmp_path / "db.json")
    d = Database(path)
    run(d, "CREATE TABLE t (id INT PRIMARY KEY, v TEXT); CREATE INDEX ON t (v);"
           "INSERT INTO t VALUES (1,'a'),(2,'b');")
    d.save()

    d2 = Database(path)
    assert q(d2, "SELECT v FROM t WHERE id = 2;").rows == [("b",)]
    assert d2.get_table("t").has_index("v")
    with pytest.raises(DBError):
        q(d2, "INSERT INTO t VALUES (1,'dup');")
