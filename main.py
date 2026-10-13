"""GopalDB - a small SQL database written from scratch in Python.

    python main.py                    interactive shell (data saved in gopaldb.json)
    python main.py script.sql         run a SQL file, then exit
    python main.py --db other.json    use a different database file
    python main.py --db :memory:      nothing is saved to disk
"""

import argparse
import sys
import time

from engine import run
from errors import DBError
from storage import Database

try:
    import readline  # noqa: F401  (gives arrow-key history in the shell)
except ImportError:
    pass

HELP = """\
Supported SQL:
  CREATE TABLE t (id INT PRIMARY KEY, name TEXT, score FLOAT);
  CREATE INDEX ON t (name);          DROP TABLE t;
  INSERT INTO t VALUES (1, 'A', 9.5), (2, 'B', 7);
  INSERT INTO t (id, name) VALUES (3, 'C');
  SELECT * | col, expr, COUNT(*), SUM/AVG/MIN/MAX(col)
         FROM t [WHERE ...] [ORDER BY col [DESC]] [LIMIT n];
  UPDATE t SET score = score + 1 WHERE id = 1;
  DELETE FROM t WHERE ...;
  SHOW TABLES;   DESCRIBE t;   EXPLAIN <statement>;
WHERE supports: = != < <= > >= AND OR NOT LIKE IS [NOT] NULL + - * /
Type 'exit' or 'bye' to quit."""


def _fmt(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def format_table(columns, rows):
    cells = [[_fmt(v) for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(texts, raw=None):
        parts = []
        for i, text in enumerate(texts):
            is_num = raw is not None and isinstance(raw[i], (int, float)) \
                and not isinstance(raw[i], bool)
            parts.append(" " + (text.rjust(widths[i]) if is_num else text.ljust(widths[i])) + " ")
        return "|" + "|".join(parts) + "|"

    out = [sep, line(columns), sep]
    out += [line(c, raw) for c, raw in zip(cells, rows)]
    out.append(sep)
    return "\n".join(out)


def show(result, elapsed):
    if result.columns:
        print(format_table(result.columns, result.rows))
        n = len(result.rows)
        print(f"{n} row{'s' if n != 1 else ''} in set ({elapsed:.2f} sec)")
    else:
        print(f"{result.message} ({elapsed:.2f} sec)")


def execute(db, sql):
    """Run SQL, print results, save to disk if anything changed."""
    start = time.perf_counter()
    try:
        results = run(db, sql)
    except DBError as e:
        print(f"ERROR: {e}")
        return False
    elapsed = time.perf_counter() - start
    for r in results:
        show(r, elapsed)
    if any(r.mutated for r in results):
        db.save()
    return True


def repl(db):
    print("Welcome to GopalDB")
    print("Type 'help' for supported SQL, 'exit' or 'bye' to quit.\n")
    buffer = []
    while True:
        try:
            line = input("gopaldb> " if not buffer else "      -> ")
        except EOFError:
            print("\nGoodbye!")
            return
        except KeyboardInterrupt:
            print("^C")
            buffer = []
            continue

        word = line.strip().rstrip(";").lower()
        if not buffer and word in ("exit", "bye", "quit"):
            print("Goodbye!")
            return
        if not buffer and word == "help":
            print(HELP)
            continue
        if not line.strip():
            continue

        buffer.append(line)
        if line.rstrip().endswith(";"):          # statement complete -> run it
            execute(db, "\n".join(buffer))
            buffer = []


def main():
    ap = argparse.ArgumentParser(description="GopalDB - SQL database in Python")
    ap.add_argument("script", nargs="?", help="SQL file to run (optional)")
    ap.add_argument("--db", default="gopaldb.json", help="database file, or :memory:")
    args = ap.parse_args()

    path = None if args.db == ":memory:" else args.db
    try:
        db = Database(path)
    except DBError as e:
        sys.exit(f"ERROR: {e}")

    if args.script:
        with open(args.script, encoding="utf-8") as f:
            ok = execute(db, f.read())
        sys.exit(0 if ok else 1)
    repl(db)


if __name__ == "__main__":
    main()
