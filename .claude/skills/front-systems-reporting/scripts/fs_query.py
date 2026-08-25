#!/usr/bin/env python3
"""Safe query helper for the Front Systems OData API.

This API returns HTTP 200 with an empty or truncated array when a query is
malformed, so mistakes look like real "no sales" answers. This script makes the
known traps structurally impossible rather than merely documented:

  * $top defaults to 2,000,000 because $top is applied BEFORE $filter
  * $skip is never used (paging cannot be consistent under that behaviour)
  * filters are built from numeric FK columns only, never display fields
  * $select excludes customer PII unless --include-pii is passed
  * revenue is computed as Qty * Price, so returns (Qty = -1) subtract

Uses curl so it works under TLS-intercepting proxies where Python's bundled CA
store fails. Credential values are never printed.

Commands
  stores  --days N                    harvest store name -> id map
  sales   --from D --to D [--stock N] transaction headers
  lines   --from D --to D [--stock N] product lines (full history via from/to)
  raw     --entity E [--filter F]     escape hatch

Dates are inclusive-from, exclusive-to, ISO (YYYY-MM-DD).
"""
import argparse
import collections
import csv
import json
import os
import subprocess
import sys

DEFAULT_TOP = 2_000_000
# Saleslines serves only a recent default window unless the endpoint's own
# from/to query parameters are set (user-discovered; 'to' is INCLUSIVE).
# History reaches back to at least 2022 with them.

PII = {"FirstName", "LastName", "Email", "Phone", "Address", "PostalCode", "City",
       "CUSTOMERID_FK", "PERSONID_FK"}

SALES_FIELDS = ["SALEID", "STOREID_FK", "POSID_FK", "SaleDate",
                "SaleDateTime", "Total", "IsVoided", "IsComplete", "IsTest",
                "IsFailed", "IsSetAside", "IsWeb"]

LINE_FIELDS = ["SALESLINEID", "SALEID", "STOREID_FK", "STOCKID_FK", "PRODUCTID_FK",
               "SaleDate", "SaleDateTime", "Qty", "Price", "FullPrice", "Discount",
               "DiscountPercent", "Cost", "VATPercent", "Currency", "IsVoided",
               "Brand", "Group", "Name", "Number", "SizeLabel", "Color", "Season",
               "Employee", "Pos", "Store", "Stock"]


def load_env(path=None):
    path = path or os.path.expanduser("~/Front Systems API/.env")
    if not os.path.exists(path):
        for alt in (".env", os.path.expanduser("~/.front-systems.env")):
            if os.path.exists(alt):
                path = alt
                break
        else:
            sys.exit(f"No .env found (looked in {path}). Needs "
                     "FRONT_SYSTEMS_SUBSCRIPTION_KEY, FRONT_SYSTEMS_API_KEY, "
                     "FRONT_SYSTEMS_BASE_URL.")
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    missing = [k for k in ("FRONT_SYSTEMS_SUBSCRIPTION_KEY", "FRONT_SYSTEMS_API_KEY",
                           "FRONT_SYSTEMS_BASE_URL") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in {path}: {', '.join(missing)}")
    return env


def query(env, entity, filt=None, select=None, top=DEFAULT_TOP, extra=None):
    """One request, no paging. Returns the value array."""
    cmd = ["curl", "-sS", "--max-time", "600", "--get",
           "-H", f"Ocp-Apim-Subscription-Key: {env['FRONT_SYSTEMS_SUBSCRIPTION_KEY']}",
           "-H", f"x-api-key: {env['FRONT_SYSTEMS_API_KEY']}",
           "-H", "Accept: application/json"]
    if filt:
        cmd += ["--data-urlencode", f"$filter={filt}"]
    if select:
        cmd += ["--data-urlencode", "$select=" + ",".join(select)]
    cmd += ["--data-urlencode", f"$top={top}"]
    for k, v in (extra or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    cmd.append(f"{env['FRONT_SYSTEMS_BASE_URL'].rstrip('/')}/odata/{entity}")

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"Request failed ({entity}). curl rc={res.returncode}. "
                 "Credentials are not shown; check network and .env.")
    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError:
        sys.exit(f"Non-JSON response from {entity}: {res.stdout[:200]}")
    if "odata.error" in payload:
        msg = payload["odata.error"].get("message", {})
        sys.exit(f"OData error on {entity}: "
                 f"{msg.get('value', msg) if isinstance(msg, dict) else msg}")
    if "value" not in payload:
        sys.exit(f"Unexpected response from {entity}: {res.stdout[:200]}")
    return payload["value"]


def date_filter(field, dfrom, dto):
    parts = []
    if dfrom:
        parts.append(f"{field} ge datetime'{dfrom}T00:00:00'")
    if dto:
        parts.append(f"{field} lt datetime'{dto}T00:00:00'")
    return parts


def num(row, key):
    v = row.get(key)
    return float(v) if v not in (None, "") else 0.0


def warn_coverage(rows, dfrom, dto, label):
    """Empty results are more often a broken query than a closed store."""
    if not rows:
        print(f"  !! {label}: 0 rows for {dfrom}..{dto}.", file=sys.stderr)
        print("     Zero is more often a bad query than no trade. Check: was a "
              "display field filtered on? For lines: are the from/to window "
              "parameters set (quoted, to-inclusive)?", file=sys.stderr)
        return
    ds = sorted({r["SaleDate"][:10] for r in rows if r.get("SaleDate")})
    print(f"  {label}: {len(rows)} rows, {ds[0]}..{ds[-1]}, {len(ds)} distinct days",
          file=sys.stderr)
    if dfrom and ds[0] > dfrom:
        print(f"     note: earliest data {ds[0]} is later than requested {dfrom}",
              file=sys.stderr)


def cmd_stores(env, args):
    """Harvest the name -> id map. No store dimension endpoint exists."""
    import datetime as dt
    since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    today = dt.date.today().isoformat()
    rows = query(env, "Saleslines", select=["Store", "Stock", "STOREID_FK", "STOCKID_FK"],
                 extra={"from": f"'{since}'", "to": f"'{today}'"})
    if not rows:
        sys.exit(f"No lines in the last {args.days} days; widen --days.")
    by_stock = collections.defaultdict(
        lambda: {"names": set(), "entities": set(), "registers": set(), "n": 0})
    for r in rows:
        e = by_stock[r["STOCKID_FK"]]
        e["names"].add(r.get("Stock"))
        e["entities"].add(r.get("Store"))
        e["registers"].add(r.get("STOREID_FK"))
        e["n"] += 1
    print(f"{'STOCKID_FK':>11}  {'lines':>6}  {'stock name':<32} {'legal entity':<30} registers")
    for sid, e in sorted(by_stock.items(), key=lambda kv: -kv[1]["n"]):
        print(f"{sid:>11}  {e['n']:>6}  {'/'.join(sorted(filter(None,e['names']))):<32} "
              f"{'/'.join(sorted(filter(None,e['entities']))):<30} "
              f"{','.join(str(x) for x in sorted(e['registers']))}")
    print("\nFilter on STOCKID_FK. Several registers map to one stock; grouping by "
          "STOREID_FK splits one shop into several.", file=sys.stderr)


def cmd_sales(env, args):
    parts = date_filter("SaleDate", args.dfrom, args.dto)
    if args.registers:
        ors = " or ".join(f"STOREID_FK eq {r}" for r in args.registers)
        parts.append(f"({ors})")
    rows = query(env, "Sales", filt=" and ".join(parts), select=SALES_FIELDS)
    warn_coverage(rows, args.dfrom, args.dto, "Sales")
    net = [r for r in rows if not r["IsVoided"]]
    daily = collections.defaultdict(lambda: [0, 0.0])
    for r in net:
        d = daily[r["SaleDate"][:10]]
        d[0] += 1
        d[1] += num(r, "Total")
    emit(args, rows, daily, len(net), sum(num(r, "Total") for r in net),
         voided=len(rows) - len(net))


def cmd_lines(env, args):
    import datetime as dt
    # --to is exclusive at the CLI; the wire parameter 'to' is inclusive.
    last = (dt.date.fromisoformat(args.dto) - dt.timedelta(days=1)).isoformat()
    window = {"from": f"'{args.dfrom}'", "to": f"'{last}'"}
    filt = f"STOCKID_FK eq {args.stock}" if args.stock else None
    fields = LINE_FIELDS + sorted(PII) if args.include_pii else LINE_FIELDS
    rows = query(env, "Saleslines", filt=filt, select=fields, extra=window)
    warn_coverage(rows, args.dfrom, args.dto, "Saleslines")
    for r in rows:
        r["LineTotal"] = round(num(r, "Qty") * num(r, "Price"), 2)
        r["LineCost"] = round(num(r, "Qty") * num(r, "Cost"), 2)
        r["LineMargin"] = round(r["LineTotal"] - r["LineCost"], 2)
    daily = collections.defaultdict(lambda: [0, 0.0])
    sales_seen = collections.defaultdict(set)
    for r in rows:
        d = r["SaleDate"][:10]
        daily[d][1] += r["LineTotal"]
        sales_seen[d].add(r["SALEID"])
    for d in daily:
        daily[d][0] = len(sales_seen[d])
    returns = [r for r in rows if num(r, "Qty") < 0]
    total = sum(r["LineTotal"] for r in rows)
    print(f"  units {sum(num(r,'Qty') for r in rows):,.0f} · "
          f"margin {sum(r['LineMargin'] for r in rows):,.2f} · "
          f"returns {len(returns)} lines worth {sum(r['LineTotal'] for r in returns):,.2f}",
          file=sys.stderr)
    emit(args, rows, daily, sum(len(v) for v in sales_seen.values()), total)


def cmd_raw(env, args):
    rows = query(env, args.entity, filt=args.filter,
                 select=args.select.split(",") if args.select else None)
    print(f"  {args.entity}: {len(rows)} rows", file=sys.stderr)
    if args.out:
        write_csv(rows, args.out)
    else:
        print(json.dumps(rows[:args.limit], ensure_ascii=False, indent=2))


def write_csv(rows, path):
    if not rows:
        print("  nothing to write", file=sys.stderr)
        return
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows, {len(cols)} cols)", file=sys.stderr)


def emit(args, rows, daily, txns, total, voided=None):
    if args.out:
        write_csv(rows, args.out)
    print(f"\n  {'date':<12}{'txns':>7}{'revenue':>16}")
    for d in sorted(daily):
        print(f"  {d:<12}{daily[d][0]:>7}{daily[d][1]:>16,.2f}")
    print(f"  {'TOTAL':<12}{txns:>7}{total:>16,.2f}")
    if voided:
        print(f"  ({voided} voided rows excluded)")
    if txns:
        print(f"  avg basket {total/txns:,.2f}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", help="path to .env")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stores", help="harvest store name -> id map")
    s.add_argument("--days", type=int, default=30)

    for name, helptext in (("sales", "transaction headers"), ("lines", "product lines")):
        q = sub.add_parser(name, help=helptext)
        q.add_argument("--from", dest="dfrom", required=True)
        q.add_argument("--to", dest="dto", required=True)
        q.add_argument("--out", help="write CSV here")
        if name == "sales":
            q.add_argument("--registers", type=int, nargs="+",
                           help="STOREID_FK values (from `stores`)")
        else:
            q.add_argument("--stock", type=int, help="STOCKID_FK (from `stores`)")
            q.add_argument("--include-pii", action="store_true",
                           help="include customer personal data — only when needed")

    r = sub.add_parser("raw", help="escape hatch")
    r.add_argument("--entity", required=True)
    r.add_argument("--filter")
    r.add_argument("--select")
    r.add_argument("--out")
    r.add_argument("--limit", type=int, default=5)

    args = p.parse_args()
    env = load_env(args.env)
    {"stores": cmd_stores, "sales": cmd_sales,
     "lines": cmd_lines, "raw": cmd_raw}[args.cmd](env, args)


if __name__ == "__main__":
    main()
