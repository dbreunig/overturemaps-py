"""Tests for the --where filter parser."""

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from overturemaps.filters import parse_where_expr, ParsedFilter


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
        with pytest.raises(ValueError, match="Could not parse"):
            parse_where_expr("just_a_key")

    def test_empty_value_raises(self):
        with pytest.raises(ValueError, match="empty value"):
            parse_where_expr("key=")
