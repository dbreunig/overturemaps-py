"""Tests for the --where filter parser."""

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from botmap.filters import parse_where_expr, ParsedFilter


class TestParseWhereExpr:
    def test_equality_string(self):
        f = parse_where_expr("categories.primary=restaurant")
        assert f == ParsedFilter(key="categories.primary", op="=", value="restaurant")

    def test_equality_int(self):
        f = parse_where_expr("num_floors=10")
        assert f.value == 10

    def test_equality_float(self):
        f = parse_where_expr("confidence=0.85")
        assert f.value == 0.85

    def test_equality_bool_true(self):
        f = parse_where_expr("has_parts=true")
        assert f.value is True

    def test_equality_bool_false(self):
        f = parse_where_expr("has_parts=false")
        assert f.value is False

    def test_not_equal(self):
        f = parse_where_expr("class!=footway")
        assert f.op == "!="
        assert f.value == "footway"

    def test_gt(self):
        f = parse_where_expr("height>100")
        assert f.op == ">"
        assert f.value == 100

    def test_gte(self):
        f = parse_where_expr("height>=100")
        assert f.op == ">="

    def test_lt(self):
        f = parse_where_expr("height<50")
        assert f.op == "<"

    def test_lte(self):
        f = parse_where_expr("height<=50")
        assert f.op == "<="

    def test_in_list(self):
        f = parse_where_expr("class in [motorway,primary,trunk]")
        assert f.op == "in"
        assert f.value == ["motorway", "primary", "trunk"]

    def test_in_list_with_spaces(self):
        f = parse_where_expr("class in [ motorway , primary ]")
        assert f.value == ["motorway", "primary"]

    def test_in_list_typed(self):
        f = parse_where_expr("num_floors in [1,2,3]")
        assert f.value == [1, 2, 3]

    def test_longest_operator_wins(self):
        # ensure `>=` isn't misread as `>` followed by `=...`
        f = parse_where_expr("height>=100")
        assert f.op == ">="
        assert f.value == 100

    def test_dotted_key(self):
        f = parse_where_expr("bbox.xmin<-70")
        assert f.key == "bbox.xmin"
        assert f.value == -70

    def test_missing_operator_raises(self):
        with pytest.raises(ValueError, match="no operator"):
            parse_where_expr("just_a_key")

    def test_missing_operator_explains_shell_redirection(self):
        # A bare key like "height" reaches the parser when the shell ate an
        # unquoted `>150` as a redirection. The error must name the key,
        # show the K OP V form, and explain the single-quote fix.
        with pytest.raises(ValueError) as exc:
            parse_where_expr("height")
        msg = str(exc.value)
        assert "height" in msg
        assert "height>150" in msg or "K OP V" in msg
        assert "single-quote" in msg or "single quote" in msg
        assert "redirect" in msg.lower()

    def test_empty_value_raises(self):
        with pytest.raises(ValueError, match="empty value"):
            parse_where_expr("key=")


class TestToPyarrowExpression:
    def test_simple_equality(self):
        schema = pa.schema([("class", pa.string())])
        f = ParsedFilter("class", "=", "motorway")
        expr = f.to_pyarrow_expression(schema)
        # Just verify it's an Expression; equality semantics are tested at
        # integration time against a real dataset.
        assert isinstance(expr, pc.Expression)

    def test_nested_field(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.primary", "=", "restaurant")
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)

    def test_in_operator(self):
        schema = pa.schema([("class", pa.string())])
        f = ParsedFilter("class", "in", ["motorway", "primary"])
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)

    def test_numeric_comparison(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height", ">", 100)
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)


class TestValidateAgainstSchema:
    def test_top_level_field_ok(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height", ">", 50)
        # Should not raise
        f.validate_against_schema(schema)

    def test_nested_field_ok(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.primary", "=", "restaurant")
        f.validate_against_schema(schema)

    def test_unknown_top_level_raises(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("widht", ">", 50)  # typo
        with pytest.raises(ValueError) as exc:
            f.validate_against_schema(schema)
        assert "widht" in str(exc.value)
        assert "available fields" in str(exc.value).lower()
        assert "height" in str(exc.value)

    def test_unknown_nested_raises(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.banana", "=", "x")
        with pytest.raises(ValueError) as exc:
            f.validate_against_schema(schema)
        assert "categories.banana" in str(exc.value)
        assert "primary" in str(exc.value)

    def test_dotted_into_non_struct_raises(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height.foo", "=", 1)
        with pytest.raises(ValueError):
            f.validate_against_schema(schema)

    def test_duplicate_segment_error_message(self):
        """Path with duplicate segments still produces the correct error position."""
        schema = pa.schema([
            ("a", pa.struct([("b", pa.int64())])),  # a.b is an int (not struct)
        ])
        f = ParsedFilter("a.b.c", "=", 1)  # trying to dot into the int
        with pytest.raises(ValueError) as exc:
            f.validate_against_schema(schema)
        assert "a.b is not a struct" in str(exc.value)
