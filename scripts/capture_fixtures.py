"""Capture real API payloads as test fixtures, with PII removed.

Run once against a live tenant. Fixtures are committed; credentials are not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from front_systems_mcp.config import load_config  # noqa: E402

PII = {"FirstName", "LastName", "Email", "Phone", "Address", "PostalCode", "City",
       "CUSTOMERID_FK", "PERSONID_FK"}
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

LINE_FIELDS = [
    "SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "SaleDate", "SaleDateTime",
    "Qty", "Price", "FullPrice", "Discount", "Cost", "VATPercent", "Currency",
    "IsVoided", "Brand", "Group", "Name", "SizeLabel", "Store", "Stock",
]
SALES_FIELDS = [
    "SALEID", "STOREID_FK", "POSID_FK", "SaleDate",
    "SaleDateTime", "Total", "IsVoided", "IsComplete",
]


def fetch(cfg, entity: str, filt: str, select: list[str], top: int) -> list[dict]:
    cmd = [
        "curl", "-sS", "--max-time", "600", "--get",
        "-H", f"Ocp-Apim-Subscription-Key: {cfg.subscription_key}",
        "-H", f"x-api-key: {cfg.api_key}",
        "-H", "Accept: application/json",
        "--data-urlencode", f"$filter={filt}",
        "--data-urlencode", "$select=" + ",".join(select),
        "--data-urlencode", f"$top={top}",
        f"{cfg.base_url}/odata/{entity}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"curl failed for {entity} (rc={res.returncode})")
    return json.loads(res.stdout)["value"]


def main() -> None:
    cfg = load_config()
    OUT.mkdir(parents=True, exist_ok=True)

    lines = fetch(
        cfg, "Saleslines",
        "STOCKID_FK eq 3229 and SaleDate ge datetime'2026-08-01T00:00:00' "
        "and SaleDate lt datetime'2026-08-04T00:00:00'",
        LINE_FIELDS, 2_000_000,
    )
    sales = fetch(
        cfg, "Sales",
        "(STOREID_FK eq 3530 or STOREID_FK eq 3568 or STOREID_FK eq 3529 "
        "or STOREID_FK eq 3431) and SaleDate ge datetime'2026-08-01T00:00:00' "
        "and SaleDate lt datetime'2026-08-04T00:00:00'",
        SALES_FIELDS, 2_000_000,
    )

    for rows in (lines, sales):
        for row in rows:
            for key in PII:
                row.pop(key, None)

    (OUT / "saleslines_sample.json").write_text(
        json.dumps({"value": lines}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "sales_sample.json").write_text(
        json.dumps({"value": sales}, ensure_ascii=False, indent=1), encoding="utf-8")

    returns = sum(1 for r in lines if float(r["Qty"]) < 0)
    print(f"saleslines: {len(lines)} rows ({returns} returns)")
    print(f"sales:      {len(sales)} rows")
    if returns == 0:
        print("WARNING: no return rows captured. Widen the date range — the "
              "Qty=-1 case is the one most likely to regress.")


if __name__ == "__main__":
    main()
