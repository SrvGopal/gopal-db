import pytest

import ast_nodes as ast
from errors import LexerError, ParseError
from lexer import tokenize
from parser import parse


def test_tokenize_basic():
    toks = tokenize("SELECT name FROM users WHERE age >= 18;")
    assert [t.type for t in toks] == ["KEYWORD", "IDENT", "KEYWORD", "IDENT", "KEYWORD",
                                      "IDENT", "OP", "NUMBER", "PUNCT", "EOF"]
    assert toks[6].value == ">="


def test_keywords_are_case_insensitive():
    assert tokenize("select")[0].value == "SELECT"


def test_string_with_escaped_quote():
    assert tokenize("'it''s'")[0].value == "it's"


def test_float_and_comment():
    toks = tokenize("3.14 -- ignored\n7")
    assert [t.value for t in toks[:2]] == [3.14, 7]


def test_unterminated_string():
    with pytest.raises(LexerError):
        tokenize("'oops")


def test_bad_character():
    with pytest.raises(LexerError):
        tokenize("SELECT @")


def test_parse_select_full():
    (s,) = parse("SELECT id, name FROM t WHERE a = 1 AND b > 2 ORDER BY id DESC LIMIT 3;")
    assert isinstance(s, ast.Select)
    assert s.table == "t" and s.limit == 3
    assert s.order_by[0][1] is True
    assert s.where.op == "AND"


def test_operator_precedence():
    (s,) = parse("SELECT a FROM t WHERE a = 1 OR b = 2 AND c = 3;")
    assert s.where.op == "OR"                 # AND binds tighter than OR
    assert s.where.right.op == "AND"
    (s,) = parse("SELECT 1 + 2 * 3 FROM t;")
    assert s.items[0].op == "+" and s.items[0].right.op == "*"


def test_parse_create_insert_update_delete():
    stmts = parse("""
        CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(20));
        INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b');
        UPDATE t SET name = 'z' WHERE id = 1;
        DELETE FROM t WHERE id = 2;
    """)
    assert [type(s).__name__ for s in stmts] == ["CreateTable", "Insert", "Update", "Delete"]
    assert stmts[0].columns[0].primary_key and stmts[0].columns[1].type == "TEXT"
    assert len(stmts[1].rows) == 2


def test_syntax_errors_have_position():
    with pytest.raises(ParseError) as e:
        parse("SELECT FROM t;")
    assert "position" in str(e.value)
    with pytest.raises(ParseError):
        parse("SELECT a FROM t WHERE;")
    with pytest.raises(ParseError):
        parse("SELECT a FROM t SELECT b FROM t;")     # missing ';'


def test_to_sql_roundtrip_labels():
    (s,) = parse("SELECT COUNT(*), age + 1 FROM t;")
    assert ast.to_sql(s.items[0]) == "COUNT(*)"
    assert ast.to_sql(s.items[1]) == "age + 1"
