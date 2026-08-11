import datetime as dt
import pytest
from front_systems_mcp.odata import (
    MAX_TOP, UnsafeQueryError, any_of, build_params, date_range, eq,
)


def test_date_range_emits_v3_datetime_literals():
    parts = date_range("SaleDate", dt.date(2026, 7, 1), dt.date(2026, 8, 1))
    assert parts == [
        "SaleDate ge datetime'2026-07-01T00:00:00'",
        "SaleDate lt datetime'2026-08-01T00:00:00'",
    ]


def test_end_is_exclusive_start_is_inclusive():
    parts = date_range("SaleDate", dt.date(2026, 7, 1), None)
    assert parts == ["SaleDate ge datetime'2026-07-01T00:00:00'"]


def test_eq_on_numeric_fk_is_allowed():
    assert eq("STOCKID_FK", 3229) == "STOCKID_FK eq 3229"


@pytest.mark.parametrize("field", ["Stock", "Store", "Brand", "Name", "Employee"])
def test_filtering_a_display_field_is_rejected(field):
    # These return HTTP 200 with an empty array rather than erroring, so a
    # silent wrong answer is the default. Fail loudly at build time instead.
    with pytest.raises(UnsafeQueryError) as exc:
        eq(field, 1)
    assert field in str(exc.value)


def test_any_of_builds_an_or_group():
    assert any_of("STOREID_FK", [3529, 3530]) == (
        "(STOREID_FK eq 3529 or STOREID_FK eq 3530)"
    )


def test_any_of_rejects_empty_values():
    with pytest.raises(UnsafeQueryError):
        any_of("STOREID_FK", [])


def test_build_params_pins_top_high_and_never_emits_skip():
    params = build_params(["SaleDate ge datetime'2026-08-01T00:00:00'"], ["SALEID"])
    assert params["$top"] == str(MAX_TOP)
    assert "$skip" not in params
    assert params["$select"] == "SALEID"
    assert params["$filter"] == "SaleDate ge datetime'2026-08-01T00:00:00'"


def test_build_params_joins_filters_with_and():
    params = build_params(["STOCKID_FK eq 1", "STOREID_FK eq 2"], ["X"])
    assert params["$filter"] == "STOCKID_FK eq 1 and STOREID_FK eq 2"


def test_build_params_requires_a_select():
    # Saleslines carries customer PII on every row; an absent $select pulls it.
    with pytest.raises(UnsafeQueryError):
        build_params(["A eq 1"], [])


def test_build_params_rejects_a_hand_built_display_field_filter():
    with pytest.raises(UnsafeQueryError) as exc:
        build_params(["Store eq 1"], ["SALEID"])
    assert "Store" in str(exc.value)


def test_build_params_rejects_a_display_field_hidden_among_valid_ones():
    with pytest.raises(UnsafeQueryError) as exc:
        build_params(["STOCKID_FK eq 3229", "Brand eq 2"], ["SALEID"])
    assert "Brand" in str(exc.value)


def test_build_params_accepts_filters_built_by_the_helpers():
    filters = [*date_range("SaleDate", dt.date(2026, 8, 1), dt.date(2026, 8, 2)),
               eq("STOCKID_FK", 3229)]
    params = build_params(filters, ["SALEID"])
    assert "STOCKID_FK eq 3229" in params["$filter"]


def test_build_params_has_no_top_override():
    import inspect
    assert "top" not in inspect.signature(build_params).parameters


def test_top_is_always_the_maximum():
    params = build_params(["STOCKID_FK eq 1"], ["SALEID"])
    assert params["$top"] == str(MAX_TOP)


@pytest.mark.parametrize("bad", [3229.9, True, "3229"])
def test_eq_rejects_non_integer_ids(bad):
    with pytest.raises(UnsafeQueryError):
        eq("STOCKID_FK", bad)


def test_any_of_rejects_non_integer_ids():
    with pytest.raises(UnsafeQueryError):
        any_of("STOREID_FK", [3529, "3530"])


def test_select_with_several_fields_is_comma_joined():
    params = build_params(["STOCKID_FK eq 1"], ["SALEID", "Total", "SaleDate"])
    assert params["$select"] == "SALEID,Total,SaleDate"


def test_date_range_with_both_bounds_none_returns_no_clauses():
    assert date_range("SaleDate", None, None) == []
