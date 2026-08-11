"""DataFrame → .png. Knows nothing about Front Systems."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display in a server process
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def write_bar_chart(frame: pd.DataFrame, x: str, y: str, title: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(frame[x].astype(str), pd.to_numeric(frame[y], errors="coerce"))
    ax.set_title(title)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
