import pytest
import pandas as pd
from openpyxl import load_workbook
from front_systems_mcp.exporters.charts import write_bar_chart
from front_systems_mcp.exporters.excel import write_workbook


def frame():
    return pd.DataFrame({"day": ["2026-08-01", "2026-08-02"], "revenue": [100.0, 250.5]})


def test_writes_one_sheet_per_frame(tmp_path):
    path = write_workbook({"Daily": frame(), "Totals": frame()}, tmp_path / "out.xlsx")
    wb = load_workbook(path)
    assert set(wb.sheetnames) >= {"Daily", "Totals"}


def test_values_are_written_not_formulas(tmp_path):
    # A data extract read by pandas must not contain formulas: openpyxl writes
    # them without cached values, so pandas would see None until Excel opens it.
    path = write_workbook({"Daily": frame()}, tmp_path / "out.xlsx")
    back = pd.read_excel(path, sheet_name="Daily")
    assert back["revenue"].tolist() == [100.0, 250.5]


def test_notes_sheet_records_assumptions(tmp_path):
    path = write_workbook({"Daily": frame()}, tmp_path / "out.xlsx",
                          notes=["Revenue is Qty * Price."])
    wb = load_workbook(path)
    assert "Notes" in wb.sheetnames
    text = " ".join(str(c.value) for row in wb["Notes"].iter_rows() for c in row)
    assert "Qty * Price" in text


def test_chart_content_reflects_the_data(tmp_path):
    # A size floor cannot distinguish a drawn chart from a blank canvas — a blank
    # canvas is actually larger. Assert instead that changing the data changes the
    # output, which a no-op renderer could not satisfy.
    a = write_bar_chart(pd.DataFrame({"day": ["x", "y"], "revenue": [100.0, 250.0]}),
                        "day", "revenue", "A", tmp_path / "a.png")
    b = write_bar_chart(pd.DataFrame({"day": ["x", "y"], "revenue": [1.0, 2.0]}),
                        "day", "revenue", "A", tmp_path / "b.png")
    assert a.read_bytes() != b.read_bytes()


def test_missing_column_raises_and_leaks_no_figure(tmp_path):
    import matplotlib.pyplot as plt
    before = len(plt.get_fignums())
    with pytest.raises(ValueError) as exc:
        write_bar_chart(pd.DataFrame({"day": ["x"], "revenue": [1.0]}),
                        "day", "nope", "T", tmp_path / "c.png")
    assert "nope" in str(exc.value)
    assert len(plt.get_fignums()) == before, "a failed render must not leak a figure"


def test_non_numeric_y_raises_rather_than_drawing_nothing(tmp_path):
    with pytest.raises(ValueError):
        write_bar_chart(pd.DataFrame({"day": ["x"], "revenue": ["not-a-number"]}),
                        "day", "revenue", "T", tmp_path / "d.png")


def test_colliding_sheet_names_are_refused(tmp_path):
    long_a = "Daily revenue by store and brand AAAA"
    long_b = "Daily revenue by store and brand BBBB"
    assert long_a[:31] == long_b[:31]
    with pytest.raises(ValueError) as exc:
        write_workbook({long_a: frame(), long_b: frame()}, tmp_path / "x.xlsx")
    assert long_a[:31] in str(exc.value)


def test_empty_frame_still_produces_a_readable_workbook(tmp_path):
    path = write_workbook({"Daily": pd.DataFrame(columns=["day", "revenue"])},
                          tmp_path / "e.xlsx")
    assert load_workbook(path)["Daily"].max_row >= 1
