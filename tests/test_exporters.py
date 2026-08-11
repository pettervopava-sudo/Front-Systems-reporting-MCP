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


def test_chart_file_is_created_and_non_trivial(tmp_path):
    path = write_bar_chart(frame(), "day", "revenue", "Revenue", tmp_path / "c.png")
    assert path.exists() and path.stat().st_size > 1000


def test_empty_frame_still_produces_a_readable_workbook(tmp_path):
    path = write_workbook({"Daily": pd.DataFrame(columns=["day", "revenue"])},
                          tmp_path / "e.xlsx")
    assert load_workbook(path)["Daily"].max_row >= 1
