"""DataFrame → .png. Knows nothing about Front Systems."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display in a server process
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def write_bar_chart(frame: pd.DataFrame, x: str, y: str, title: str, path: Path) -> Path:
    # Validate columns exist before allocating figure to prevent leaks on error.
    if x not in frame.columns:
        raise ValueError(
            f"Column '{x}' not found. Available columns: {list(frame.columns)}"
        )
    if y not in frame.columns:
        raise ValueError(
            f"Column '{y}' not found. Available columns: {list(frame.columns)}"
        )

    # Validate y is numeric after coercion; all-NaN would draw nothing.
    y_numeric = pd.to_numeric(frame[y], errors="coerce")
    if y_numeric.isna().all():
        raise ValueError(
            f"Column '{y}' contains no numeric values after coercion"
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 5))
    try:
        ax.bar(frame[x].astype(str), y_numeric)
        ax.set_title(title)
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        ax.grid(axis="y", alpha=0.3)
        fig.autofmt_xdate(rotation=45)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)
    return path
