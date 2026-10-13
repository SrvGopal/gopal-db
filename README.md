# GopalDB. (AI se README banwaya hai baki sab mera hai!)

A small relational database with a SQL interface, written from scratch in Python
(no SQLite, no parsing libraries). Built to understand how databases like MySQL
work internally: how SQL text becomes tokens, a syntax tree, a query plan, and
finally rows.

```
 SQL text
    │
    ▼
 lexer.py       tokens           SELECT  name  FROM  users ...
    │
    ▼
 parser.py      AST              recursive-descent parser, operator precedence
    │
    ▼
 planner.py     logical plan     Limit → Project → Sort → Filter → SeqScan
    │                            (+ semantic checks: unknown table/column)
    ▼
 optimizer.py   better plan      constant folding, dead-filter removal, index selection
    │
    ▼
 executor.py    results          iterator ("Volcano") model: operators stream rows
    │
    ▼
 storage.py     tables, hash indexes, primary keys, JSON persistence
```

## Run it

```bash
python main.py                          # interactive shell, data saved to gopaldb.json
python main.py examples/demo.sql --db :memory:   # run a script, nothing saved
pip install pytest && pytest            # run the test-suite
```

## Example

```
gopaldb> CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT);
gopaldb> INSERT INTO users VALUES (1,'Asha',25), (2,'Ravi',31), (3,'Meera',25);
gopaldb> SELECT name, age + 1 FROM users WHERE age = 25 ORDER BY name DESC;
+-------+---------+
| name  | age + 1 |
+-------+---------+
| Meera |      26 |
| Asha  |      26 |
+-------+---------+
2 rows in set (0.00 sec)

gopaldb> EXPLAIN SELECT * FROM users WHERE id = 2;
+-------------------------------------------+
| QUERY PLAN                                |
+-------------------------------------------+
| Project: id, name, age                    |
| └─ IndexScan on users using index(id) = 2 |
+-------------------------------------------+
```

## Supported SQL

| Area | Features |
|------|----------|
| DDL | `CREATE TABLE` (INT, FLOAT, TEXT/VARCHAR, `PRIMARY KEY`), `DROP TABLE`, `CREATE INDEX ON t (col)` |
| DML | `INSERT` (multi-row, optional column list), `UPDATE ... SET col = expr`, `DELETE` |
| Queries | `SELECT` columns / `*` / expressions, `WHERE`, `ORDER BY` (multi-key, ASC/DESC), `LIMIT`, `COUNT/SUM/AVG/MIN/MAX` |
| Expressions | `= != <> < <= > >=`, `AND OR NOT`, `LIKE` (`%`, `_`), `IS [NOT] NULL`, `+ - * /`, parentheses |
| Utility | `SHOW TABLES`, `DESCRIBE`, `EXPLAIN` |

## Design decisions worth talking about

- **Recursive-descent parser** with explicit precedence levels (OR < AND < NOT < comparison < + - < * /), and syntax errors that report the position and what was expected.
- **Separate planner and optimizer.** The planner builds a tree of operators and validates names; the optimizer rewrites that tree. `EXPLAIN` prints the plan so you can see what the optimizer did.
- **Optimizer rules:** constant folding (`age > 10 + 10` → `age > 20`), boolean simplification, removing always-true filters, skipping the scan for always-false filters, and picking an `IndexScan` for `column = literal` (primary key preferred), keeping the remaining `AND` conditions as a filter.
- **Hash indexes** (`value → set(row_ids)`) kept in sync on every insert / update / delete. Equality lookups are O(1) instead of O(n).
- **Iterator execution model:** each operator is a generator pulling rows from its child, so `LIMIT` stops the pipeline early.
- **SQL NULL semantics** (three-valued logic): `NULL = NULL` is not true, `NULL AND FALSE` is false, aggregates skip NULLs, NULLs sort first.
- **Statement atomicity:** a multi-row `INSERT` or `UPDATE` that fails halfway (e.g. duplicate primary key) is rolled back.
- **Crash-safe persistence:** the database is written to a temp file and atomically renamed over the old one.

## Limitations / roadmap

- No `JOIN`, `GROUP BY`, `HAVING`, subqueries, or `NOT NULL` / `UNIQUE` constraints yet.
- No transactions or write-ahead log; the whole database is rewritten on each change.
- Indexes are hash-based (equality only); a B-tree would also speed up range queries and `ORDER BY`.
- Table and column names are case-sensitive; `KEY`, `INDEX`, `TEXT`, `COUNT`... are reserved words.

## Project layout

```
main.py          interactive shell / script runner, MySQL-style table output
engine.py        wires the pipeline together: run(db, sql)
lexer.py         tokenizer
parser.py        parser
ast_nodes.py     AST node classes + helpers
planner.py       plan nodes, semantic checks, EXPLAIN rendering
optimizer.py     plan rewrites
executor.py      operator execution (scan, filter, sort, project, aggregate, DML)
expressions.py   expression evaluator (NULL logic, LIKE, arithmetic)
storage.py       Table, Database, indexes, persistence
errors.py        exception hierarchy
tests/           pytest suite (lexer, parser, engine, optimizer)
examples/        demo.sql
```
