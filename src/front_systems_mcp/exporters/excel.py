"""DataFrame → .xlsx. Knows nothing about Front Systems."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="1F3864")


def write_workbook(
    frames: dict[str, pd.DataFrame],
    path: Path,
    notes: Sequence[str] = (),
) -> Path:
    """Write one sheet per frame, as values.

    Values rather than formulas: these workbooks are read back by pandas, and
    openpyxl writes formulas with no cached value, so a formula cell reads as
    None until Excel opens the file.
    """
    # Check for sheet-name collisions: two distinct names truncating to the same
    # 31 characters would silently overwrite each other.
    truncated_names = {}
    for name in frames.keys():
        truncated = name[:31]
        if truncated in truncated_names and truncated_names[truncated] != name:
            raise ValueError(
                f"Sheet-name collision: '{truncated_names[truncated]}' and '{name}' "
                f"both truncate to '{truncated}' (Excel limit: 31 characters)"
            )
        truncated_names[truncated] = name

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
        if notes:
            pd.DataFrame({"Notes & assumptions": list(notes)}).to_excel(
                writer, sheet_name="Notes", index=False)

        for sheet in writer.book.worksheets:
            for cell in sheet[1]:
                cell.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
                cell.fill = HEADER_FILL
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
            for column in sheet.columns:
                width = max((len(str(c.value)) for c in column if c.value), default=8)
                sheet.column_dimensions[column[0].column_letter].width = min(
                    max(width + 3, 10), 24)
            sheet.freeze_panes = "A2"
    return path
