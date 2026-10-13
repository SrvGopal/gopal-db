"""Parser: list of Tokens -> AST.

A hand-written recursive-descent parser. Expression precedence (low -> high):

    OR  <  AND  <  NOT  <  comparison (= != < <= > >= LIKE IS NULL)  <  + -  <  * /  <  unary -
"""

from ast_nodes import (
    Aggregate, BinaryOp, ColumnDef, ColumnRef, CreateIndex, CreateTable, Delete,
    Describe, DropTable, Explain, Insert, IsNull, Literal, Select, ShowTables,
    Star, UnaryOp, Update,
)
from errors import ParseError
from lexer import tokenize

COMPARISON_OPS = {"=", "!=", "<", "<=", ">", ">="}
AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
TYPE_NAMES = {"INT": "INT", "INTEGER": "INT", "FLOAT": "FLOAT",
              "TEXT": "TEXT", "VARCHAR": "TEXT"}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    # ---------------------------------------------------------------- helpers
    def peek(self):
        return self.tokens[self.i]

    def advance(self):
        tok = self.tokens[self.i]
        if tok.type != "EOF":
            self.i += 1
        return tok

    def at_end(self):
        return self.peek().type == "EOF"

    def error(self, expected):
        tok = self.peek()
        found = "end of input" if tok.type == "EOF" else f"'{tok.value}'"
        raise ParseError(f"Syntax error at position {tok.pos}: expected {expected}, found {found}")

    def check_kw(self, *names):
        tok = self.peek()
        return tok.type == "KEYWORD" and tok.value in names

    def match_kw(self, *names):
        if self.check_kw(*names):
            self.advance()
            return True
        return False

    def expect_kw(self, name):
        if not self.match_kw(name):
            self.error(name)

    def check_op(self, *ops):
        tok = self.peek()
        return tok.type == "OP" and tok.value in ops

    def match_op(self, *ops):
        if self.check_op(*ops):
            return self.advance().value
        return None

    def match_punct(self, ch):
        tok = self.peek()
        if tok.type == "PUNCT" and tok.value == ch:
            self.advance()
            return True
        return False

    def expect_punct(self, ch):
        if not self.match_punct(ch):
            self.error(f"'{ch}'")

    def expect_ident(self):
        tok = self.peek()
        if tok.type != "IDENT":
            self.error("an identifier")
        return self.advance().value

    # ------------------------------------------------------------- statements
    def parse_script(self):
        """Parse everything: one or more statements separated by ';'."""
        statements = []
        while not self.at_end():
            if self.match_punct(";"):
                continue
            statements.append(self.parse_statement())
            if not self.at_end():
                self.expect_punct(";")
        return statements

    def parse_statement(self):
        tok = self.peek()
        if tok.type == "KEYWORD":
            handler = {
                "SELECT": self.parse_select,
                "INSERT": self.parse_insert,
                "UPDATE": self.parse_update,
                "DELETE": self.parse_delete,
                "CREATE": self.parse_create,
                "DROP": self.parse_drop,
                "SHOW": self.parse_show,
                "DESCRIBE": self.parse_describe,
                "EXPLAIN": self.parse_explain,
            }.get(tok.value)
            if handler:
                return handler()
        self.error("a statement (SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, SHOW, DESCRIBE, EXPLAIN)")

    def parse_select(self):
        self.expect_kw("SELECT")
        items = []
        while True:
            if self.match_op("*"):
                items.append(Star())
            else:
                items.append(self.parse_expression())
            if not self.match_punct(","):
                break
        self.expect_kw("FROM")
        table = self.expect_ident()

        where = self.parse_expression() if self.match_kw("WHERE") else None

        order_by = []
        if self.match_kw("ORDER"):
            self.expect_kw("BY")
            while True:
                expr = self.parse_expression()
                desc = False
                if self.match_kw("DESC"):
                    desc = True
                else:
                    self.match_kw("ASC")
                order_by.append((expr, desc))
                if not self.match_punct(","):
                    break

        limit = None
        if self.match_kw("LIMIT"):
            tok = self.peek()
            if tok.type != "NUMBER" or not isinstance(tok.value, int):
                self.error("an integer after LIMIT")
            limit = self.advance().value

        return Select(items, table, where, order_by, limit)

    def parse_insert(self):
        self.expect_kw("INSERT")
        self.expect_kw("INTO")
        table = self.expect_ident()

        columns = None
        if self.match_punct("("):
            columns = [self.expect_ident()]
            while self.match_punct(","):
                columns.append(self.expect_ident())
            self.expect_punct(")")

        self.expect_kw("VALUES")
        rows = []
        while True:
            self.expect_punct("(")
            row = [self.parse_expression()]
            while self.match_punct(","):
                row.append(self.parse_expression())
            self.expect_punct(")")
            rows.append(row)
            if not self.match_punct(","):
                break
        return Insert(table, columns, rows)

    def parse_update(self):
        self.expect_kw("UPDATE")
        table = self.expect_ident()
        self.expect_kw("SET")
        assignments = []
        while True:
            col = self.expect_ident()
            if not self.match_op("="):
                self.error("'='")
            assignments.append((col, self.parse_expression()))
            if not self.match_punct(","):
                break
        where = self.parse_expression() if self.match_kw("WHERE") else None
        return Update(table, assignments, where)

    def parse_delete(self):
        self.expect_kw("DELETE")
        self.expect_kw("FROM")
        table = self.expect_ident()
        where = self.parse_expression() if self.match_kw("WHERE") else None
        return Delete(table, where)

    def parse_create(self):
        self.expect_kw("CREATE")
        if self.match_kw("TABLE"):
            return self.parse_create_table()
        if self.match_kw("INDEX"):
            name = self.expect_ident() if self.peek().type == "IDENT" else None
            self.expect_kw("ON")
            table = self.expect_ident()
            self.expect_punct("(")
            column = self.expect_ident()
            self.expect_punct(")")
            return CreateIndex(table, column, name)
        self.error("TABLE or INDEX")

    def parse_create_table(self):
        name = self.expect_ident()
        self.expect_punct("(")
        columns = []
        while True:
            col_name = self.expect_ident()
            col_type = self.parse_type()
            primary = False
            if self.match_kw("PRIMARY"):
                self.expect_kw("KEY")
                primary = True
            columns.append(ColumnDef(col_name, col_type, primary))
            if not self.match_punct(","):
                break
        self.expect_punct(")")
        return CreateTable(name, columns)

    def parse_type(self):
        tok = self.peek()
        if tok.type == "KEYWORD" and tok.value in TYPE_NAMES:
            self.advance()
            if tok.value == "VARCHAR" and self.match_punct("("):   # VARCHAR(255): size ignored
                if self.peek().type != "NUMBER":
                    self.error("a size")
                self.advance()
                self.expect_punct(")")
            return TYPE_NAMES[tok.value]
        self.error("a column type (INT, FLOAT, TEXT, VARCHAR)")

    def parse_drop(self):
        self.expect_kw("DROP")
        self.expect_kw("TABLE")
        return DropTable(self.expect_ident())

    def parse_show(self):
        self.expect_kw("SHOW")
        self.expect_kw("TABLES")
        return ShowTables()

    def parse_describe(self):
        self.expect_kw("DESCRIBE")
        return Describe(self.expect_ident())

    def parse_explain(self):
        self.expect_kw("EXPLAIN")
        return Explain(self.parse_statement())

    # ------------------------------------------------------------ expressions
    def parse_expression(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.match_kw("OR"):
            left = BinaryOp("OR", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.match_kw("AND"):
            left = BinaryOp("AND", left, self.parse_not())
        return left

    def parse_not(self):
        if self.match_kw("NOT"):
            return UnaryOp("NOT", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        op = self.match_op(*COMPARISON_OPS)
        if op:
            return BinaryOp(op, left, self.parse_additive())
        if self.match_kw("IS"):
            negated = self.match_kw("NOT")
            self.expect_kw("NULL")
            return IsNull(left, negated)
        if self.match_kw("LIKE"):
            return BinaryOp("LIKE", left, self.parse_additive())
        return left

    def parse_additive(self):
        left = self.parse_term()
        while True:
            op = self.match_op("+", "-")
            if not op:
                return left
            left = BinaryOp(op, left, self.parse_term())

    def parse_term(self):
        left = self.parse_unary()
        while True:
            op = self.match_op("*", "/")
            if not op:
                return left
            left = BinaryOp(op, left, self.parse_unary())

    def parse_unary(self):
        if self.match_op("-"):
            return UnaryOp("-", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()

        if tok.type == "NUMBER" or tok.type == "STRING":
            self.advance()
            return Literal(tok.value)

        if tok.type == "KEYWORD":
            if tok.value == "NULL":
                self.advance()
                return Literal(None)
            if tok.value == "TRUE":
                self.advance()
                return Literal(True)
            if tok.value == "FALSE":
                self.advance()
                return Literal(False)
            if tok.value in AGGREGATES:
                return self.parse_aggregate()

        if tok.type == "IDENT":
            self.advance()
            return ColumnRef(tok.value)

        if self.match_punct("("):
            expr = self.parse_expression()
            self.expect_punct(")")
            return expr

        self.error("an expression")

    def parse_aggregate(self):
        func = self.advance().value
        self.expect_punct("(")
        if func == "COUNT" and self.match_op("*"):
            arg = None
        else:
            arg = self.parse_expression()
        self.expect_punct(")")
        return Aggregate(func, arg)


def parse(sql):
    """Convenience: SQL text -> list of statement ASTs."""
    return Parser(tokenize(sql)).parse_script()
