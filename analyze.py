import os
import csv
import json
import math
import argparse
from datetime import datetime, timezone

SECONDS_PER_YEAR = 365.25 * 24 * 3600
GAP_TOLERANCE    = 3.0


def loadRun(runDir: str):
    meta = {}
    metaPath = os.path.join(runDir, "meta.json")
    if os.path.exists(metaPath):
        with open(metaPath) as f:
            meta = json.load(f)

    times, values = [], []
    eqPath = os.path.join(runDir, "equity.parquet")
    if os.path.exists(eqPath):
        import pyarrow.parquet as pq
        t = pq.read_table(eqPath)
        times  = t.column("time").to_pylist()
        values = t.column("equity").to_pylist()

    fills = []
    fillPath = os.path.join(runDir, "fills.csv")
    if os.path.exists(fillPath):
        with open(fillPath, newline="") as f:
            for row in csv.DictReader(f):
                fills.append({
                    "time":     int(row["time"]),
                    "conId":    int(row["conId"]),
                    "symbol":   row["symbol"],
                    "action":   row["action"],
                    "qty":      int(row["qty"]),
                    "price":    float(row["price"]),
                    "commission": float(row.get("commission") or 0.0),
                    "position": int(row["position"]),
                    "equity":   float(row["equity"]),
                })

    return meta, times, values, fills


def equityStats(times, values) -> dict:
    n = len(values)
    if n < 2:
        return {"samples": n}

    spacings = [times[i] - times[i - 1] for i in range(1, n)]
    nominal  = sorted(spacings)[len(spacings) // 2] or 1

    rets, kept, skipped = [], 0, 0
    for i in range(1, n):
        dt = times[i] - times[i - 1]
        if dt > nominal * GAP_TOLERANCE:
            skipped += 1
            continue
        prev = values[i - 1]
        if prev:
            rets.append((values[i] - prev) / prev)
            kept += 1

    span    = max(times[-1] - times[0], 1)
    years   = span / SECONDS_PER_YEAR
    perYear = (kept / years) if years > 0 else 0.0

    mean = sum(rets) / len(rets) if rets else 0.0
    var  = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    std  = math.sqrt(var)
    down = [r for r in rets if r < 0]
    dvar = sum(r * r for r in down) / len(down) if down else 0.0
    dstd = math.sqrt(dvar)

    scale   = math.sqrt(perYear) if perYear > 0 else 0.0
    sharpe  = (mean / std * scale) if std else 0.0
    sortino = (mean / dstd * scale) if dstd else 0.0

    peak, maxdd = values[0], 0.0
    peakAt, ddStart, ddLongest = times[0], times[0], 0
    for t, v in zip(times, values):
        if v >= peak:
            peak, peakAt = v, t
            ddStart = t
        else:
            ddLongest = max(ddLongest, t - ddStart)
            if peak:
                maxdd = max(maxdd, (peak - v) / peak)

    start, end = values[0], values[-1]
    totalRet   = (end / start - 1.0) if start else 0.0
    cagr       = ((end / start) ** (1 / years) - 1.0) if start and years > 0 and end > 0 else 0.0

    return {
        "samples":     n,
        "start":       start,
        "end":         end,
        "startTime":   times[0],
        "endTime":     times[-1],
        "days":        span / 86400,
        "totalReturn": totalRet,
        "cagr":        cagr,
        "sharpe":      sharpe,
        "sortino":     sortino,
        "maxDrawdown": maxdd,
        "ddDays":      ddLongest / 86400,
        "perYear":     perYear,
        "gapsSkipped": skipped,
        "bestReturn":  max(rets) if rets else 0.0,
        "worstReturn": min(rets) if rets else 0.0,
    }


def tradeStats(fills, marks) -> dict:
    positions, avgCost, realised = {}, {}, {}
    volume, roundTrips, commission = 0.0, [], 0.0
    symbols = {}

    for f in fills:
        cid = f["conId"]
        symbols[cid] = f["symbol"]
        q = f["qty"] if f["action"] == "BUY" else -f["qty"]
        x = f["price"]
        volume += abs(q) * x
        commission += f.get("commission", 0.0)

        p = positions.get(cid, 0)
        c = avgCost.get(cid, 0.0)

        if p == 0 or (p > 0) == (q > 0):
            total = p + q
            avgCost[cid]   = ((p * c) + (q * x)) / total if total else 0.0
            positions[cid] = total
        else:
            closed = min(abs(p), abs(q))
            pnl    = closed * (x - c) * (1 if p > 0 else -1)
            realised[cid] = realised.get(cid, 0.0) + pnl
            roundTrips.append(pnl)
            remaining = p + q
            if (remaining > 0) != (p > 0) and remaining != 0:
                avgCost[cid] = x
            positions[cid] = remaining
            if remaining == 0:
                avgCost[cid] = 0.0

    unrealised = {}
    for cid, p in positions.items():
        if p and marks.get(str(cid), marks.get(cid)) is not None:
            mark = float(marks.get(str(cid), marks.get(cid)))
            unrealised[cid] = p * (mark - avgCost.get(cid, 0.0))

    wins   = [p for p in roundTrips if p > 0]
    losses = [p for p in roundTrips if p < 0]

    perInstrument = {}
    for cid in set(list(realised) + list(unrealised)):
        perInstrument[cid] = {
            "symbol":     symbols.get(cid, str(cid)),
            "realised":   realised.get(cid, 0.0),
            "unrealised": unrealised.get(cid, 0.0),
            "total":      realised.get(cid, 0.0) + unrealised.get(cid, 0.0),
            "position":   positions.get(cid, 0),
        }

    return {
        "fills":         len(fills),
        "volume":        volume,
        "roundTrips":    len(roundTrips),
        "winRate":       (len(wins) / len(roundTrips)) if roundTrips else 0.0,
        "avgWin":        (sum(wins) / len(wins)) if wins else 0.0,
        "avgLoss":       (sum(losses) / len(losses)) if losses else 0.0,
        "realisedTotal": sum(realised.values()),
        "commission":    commission,
        "openPositions": {c: p for c, p in positions.items() if p},
        "perInstrument": perInstrument,
    }


def _fmtTime(epoch) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M")


def printReport(meta, eq, tr) -> None:
    print()
    print("=" * 64)
    print(f"  RUN {meta.get('name', '')}".rstrip())
    if meta.get("source"):
        print(f"  source {meta['source']}  range {meta.get('from','?')} .. {meta.get('to','?')}")
    if meta.get("signalSource"):
        print(f"  signal {meta['signalSource']}")
    print("=" * 64)

    if eq.get("samples", 0) < 2:
        print("  not enough equity samples for statistics")
    else:
        print(f"  period            {_fmtTime(eq['startTime'])} .. {_fmtTime(eq['endTime'])}"
              f"  ({eq['days']:.1f} days)")
        print(f"  samples           {eq['samples']:,}  ({eq['perYear']:,.0f}/yr effective)")
        if eq["gapsSkipped"]:
            print(f"  gaps excluded     {eq['gapsSkipped']:,} sample intervals")
        print()
        print(f"  start equity      {eq['start']:>16,.2f}")
        print(f"  end equity        {eq['end']:>16,.2f}")
        print(f"  total return      {eq['totalReturn'] * 100:>15.3f}%")
        print(f"  CAGR              {eq['cagr'] * 100:>15.3f}%")
        print()
        print(f"  Sharpe (annual)   {eq['sharpe']:>16.3f}")
        print(f"  Sortino (annual)  {eq['sortino']:>16.3f}")
        print(f"  max drawdown      {eq['maxDrawdown'] * 100:>15.3f}%")
        print(f"  longest drawdown  {eq['ddDays']:>15.1f} days")

    print()
    print(f"  fills             {tr['fills']:>16,}")
    print(f"  round trips       {tr['roundTrips']:>16,}")
    print(f"  traded volume     {tr['volume']:>16,.0f}")
    print(f"  commission        {tr['commission']:>16,.2f}")
    net = tr['realisedTotal'] - tr['commission']
    print(f"  realised net      {net:>16,.2f}  (gross {tr['realisedTotal']:,.2f})")
    if tr["roundTrips"]:
        print(f"  win rate          {tr['winRate'] * 100:>15.1f}%")
        print(f"  avg win / loss    {tr['avgWin']:>10,.2f} / {tr['avgLoss']:,.2f}")

    if tr["perInstrument"]:
        print()
        print("  PER INSTRUMENT")
        rows = sorted(tr["perInstrument"].values(), key=lambda r: -r["total"])
        print(f"    {'symbol':<10}{'realised':>14}{'unrealised':>14}{'total':>14}{'position':>12}")
        for r in rows:
            print(f"    {r['symbol']:<10}{r['realised']:>14,.2f}{r['unrealised']:>14,.2f}"
                  f"{r['total']:>14,.2f}{r['position']:>12,}")
    print("=" * 64)


def _downsample(times, values, target=1500):
    n = len(values)
    if n <= target:
        return list(zip(times, values))
    step = n / target
    out  = []
    for i in range(target):
        lo = int(i * step)
        hi = max(lo + 1, int((i + 1) * step))
        chunk = values[lo:hi]
        out.append((times[lo], min(chunk)))
        out.append((times[hi - 1], max(chunk)))
    return out


def _polyline(points, w, h, pad, invert=False):
    if not points:
        return "", 0, 0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 == x0:
        x1 = x0 + 1
    if y1 == y0:
        y1 = y0 + 1
    coords = []
    for x, y in points:
        px = pad + (x - x0) / (x1 - x0) * (w - 2 * pad)
        fy = (y - y0) / (y1 - y0)
        py = pad + (fy if invert else 1 - fy) * (h - 2 * pad)
        coords.append(f"{px:.1f},{py:.1f}")
    return " ".join(coords), y0, y1


def _chart(title, points, w=900, h=260, colour="#2a6fb0", invert=False):
    pad = 34
    path, lo, hi = _polyline(points, w, h, pad, invert)
    if not path:
        return f"<h3>{title}</h3><p>no data</p>"
    return f"""<h3>{title}</h3>
<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">
<rect x="{pad}" y="{pad}" width="{w-2*pad}" height="{h-2*pad}" fill="none" stroke="#ddd"/>
<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="1.2"/>
<text x="{pad}" y="{pad-8}" font-size="11" fill="#666">{hi:,.2f}</text>
<text x="{pad}" y="{h-pad+14}" font-size="11" fill="#666">{lo:,.2f}</text>
</svg>"""


def _bars(title, rows, w=900, barH=18):
    if not rows:
        return ""
    h = len(rows) * (barH + 4) + 30
    mx = max(abs(r["total"]) for r in rows) or 1.0
    mid = w * 0.45
    out = [f'<h3>{title}</h3>', f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">']
    for i, r in enumerate(rows):
        y = 10 + i * (barH + 4)
        length = abs(r["total"]) / mx * (w * 0.45)
        x = mid if r["total"] >= 0 else mid - length
        colour = "#2e7d46" if r["total"] >= 0 else "#b03a2a"
        out.append(f'<rect x="{x:.1f}" y="{y}" width="{length:.1f}" height="{barH}" fill="{colour}"/>')
        out.append(f'<text x="{mid-8:.0f}" y="{y+barH-4}" font-size="11" text-anchor="end" '
                   f'fill="#333">{r["symbol"]}</text>')
        out.append(f'<text x="{mid+w*0.47:.0f}" y="{y+barH-4}" font-size="11" text-anchor="end" '
                   f'fill="#333">{r["total"]:,.0f}</text>')
    out.append(f'<line x1="{mid}" y1="0" x2="{mid}" y2="{h}" stroke="#bbb"/>')
    out.append("</svg>")
    return "\n".join(out)


def writeHtml(path, meta, eq, tr, times, values) -> str:
    curve = _downsample(times, values)

    dd, peak = [], values[0] if values else 0.0
    for t, v in zip(times, values):
        peak = max(peak, v)
        dd.append((t, (v - peak) / peak * 100 if peak else 0.0))
    ddCurve = _downsample([d[0] for d in dd], [d[1] for d in dd])

    rows = sorted(tr["perInstrument"].values(), key=lambda r: -r["total"])

    def row(label, value):
        return f"<tr><td>{label}</td><td class='n'>{value}</td></tr>"

    summary = [
        row("period", f"{_fmtTime(eq['startTime'])} .. {_fmtTime(eq['endTime'])}") if eq.get("samples", 0) > 1 else "",
        row("total return", f"{eq.get('totalReturn', 0)*100:.3f}%"),
        row("CAGR", f"{eq.get('cagr', 0)*100:.3f}%"),
        row("Sharpe (annual)", f"{eq.get('sharpe', 0):.3f}"),
        row("Sortino (annual)", f"{eq.get('sortino', 0):.3f}"),
        row("max drawdown", f"{eq.get('maxDrawdown', 0)*100:.3f}%"),
        row("longest drawdown", f"{eq.get('ddDays', 0):.1f} days"),
        row("fills", f"{tr['fills']:,}"),
        row("round trips", f"{tr['roundTrips']:,}"),
        row("win rate", f"{tr['winRate']*100:.1f}%") if tr["roundTrips"] else "",
        row("traded volume", f"{tr['volume']:,.0f}"),
        row("commission", f"{tr['commission']:,.2f}"),
    ]

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Backtest {meta.get('name','')}</title>
<style>
body {{ font-family: ui-monospace, Menlo, Consolas, monospace; margin: 30px auto; max-width: 960px;
        color: #222; background: #fafafa; }}
h2 {{ font-weight: 600; margin-bottom: 4px; }}
h3 {{ font-weight: 600; font-size: 14px; margin: 28px 0 6px; }}
p.sub {{ color: #666; margin-top: 0; font-size: 13px; }}
table {{ border-collapse: collapse; font-size: 13px; }}
td {{ padding: 3px 18px 3px 0; }}
td.n {{ text-align: right; }}
</style></head><body>
<h2>Backtest {meta.get('name','')}</h2>
<p class="sub">{meta.get('source','')} &middot; {meta.get('from','')} .. {meta.get('to','')}
&middot; {meta.get('signalSource','')}</p>
<table>{''.join(summary)}</table>
{_chart("Equity", curve)}
{_chart("Drawdown (%)", ddCurve, colour="#b03a2a")}
{_bars("P&amp;L by instrument", rows)}
</body></html>"""

    with open(path, "w") as f:
        f.write(html)
    return path


def collectRuns(resultsDir: str):
    runs = []
    for name in sorted(os.listdir(resultsDir)):
        runDir = os.path.join(resultsDir, name)
        if not os.path.isdir(runDir):
            continue
        try:
            meta, times, values, fills = loadRun(runDir)
        except Exception:
            continue
        meta.setdefault("name", name)
        eq = equityStats(times, values)
        tr = tradeStats(fills, meta.get("marks", {}))
        curve = _downsample(times, values, 900) if values else []
        dd = []
        if values:
            peak = values[0]
            dts, dvs = [], []
            for t, v in zip(times, values):
                peak = max(peak, v)
                dts.append(t)
                dvs.append((v - peak) / peak * 100 if peak else 0.0)
            dd = _downsample(dts, dvs, 900)
        runs.append({
            "dir": runDir, "name": meta["name"], "meta": meta,
            "eq": eq, "tr": tr, "curve": curve, "dd": dd,
            "mtime": os.path.getmtime(runDir),
        })
    return runs


INDEX_COLUMNS = [
    ("name",   "run",      False),
    ("mode",   "mode",     False),
    ("signal", "signal",   False),
    ("range",  "range",    False),
    ("days",   "days",     True),
    ("ret",    "return %", True),
    ("cagr",   "CAGR %",   True),
    ("sharpe", "Sharpe",   True),
    ("maxdd",  "maxDD %",  True),
    ("fills",  "fills",    True),
    ("win",    "win %",    True),
]

SORT_KEYS = {
    "name":   lambda r: r["name"],
    "date":   lambda r: r["mtime"],
    "days":   lambda r: r["eq"].get("days", 0),
    "return": lambda r: r["eq"].get("totalReturn", 0),
    "cagr":   lambda r: r["eq"].get("cagr", 0),
    "sharpe": lambda r: r["eq"].get("sharpe", 0),
    "maxdd":  lambda r: r["eq"].get("maxDrawdown", 0),
    "fills":  lambda r: r["tr"]["fills"],
    "win":    lambda r: r["tr"]["winRate"],
}


def sortRuns(runs, key: str, ascending: bool = False):
    if key not in SORT_KEYS:
        raise SystemExit(f"Unknown sort key '{key}'. Options: {', '.join(sorted(SORT_KEYS))}")
    textual = key == "name"
    return sorted(runs, key=SORT_KEYS[key], reverse=not (ascending or textual))


def _indexCells(r):
    eq, tr, meta = r["eq"], r["tr"], r["meta"]
    has = eq.get("samples", 0) > 1
    return {
        "name":   r["name"],
        "mode":   meta.get("mode", ""),
        "signal": meta.get("signalSource", ""),
        "range":  f"{meta.get('from','')} .. {meta.get('to','')}".strip(" ."),
        "days":   f"{eq['days']:.1f}" if has else "",
        "ret":    f"{eq['totalReturn']*100:.3f}" if has else "",
        "cagr":   f"{eq['cagr']*100:.2f}" if has else "",
        "sharpe": f"{eq['sharpe']:.2f}" if has else "",
        "maxdd":  f"{eq['maxDrawdown']*100:.3f}" if has else "",
        "fills":  f"{tr['fills']}",
        "win":    f"{tr['winRate']*100:.1f}" if tr["roundTrips"] else "",
    }


def _indexRow(r):
    vals  = _indexCells(r)
    cells = []
    for key, _, numeric in INDEX_COLUMNS:
        cls = " class='n'" if numeric else ""
        cells.append(f"<td{cls}>{vals[key]}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _indexPanel(r, openFirst=False):
    tail = sorted(r["tr"]["perInstrument"].values(), key=lambda x: -x["total"])
    meta, eq, tr = r["meta"], r["eq"], r["tr"]
    has = eq.get("samples", 0) > 1

    def row(label, value):
        return f"<tr><td>{label}</td><td class='n'>{value}</td></tr>"

    summary = "".join([
        row("period", f"{_fmtTime(eq['startTime'])} .. {_fmtTime(eq['endTime'])}") if has else "",
        row("total return", f"{eq.get('totalReturn',0)*100:.3f}%") if has else "",
        row("CAGR", f"{eq.get('cagr',0)*100:.3f}%") if has else "",
        row("Sharpe (annual)", f"{eq.get('sharpe',0):.3f}") if has else "",
        row("Sortino (annual)", f"{eq.get('sortino',0):.3f}") if has else "",
        row("max drawdown", f"{eq.get('maxDrawdown',0)*100:.3f}%") if has else "",
        row("longest drawdown", f"{eq.get('ddDays',0):.1f} days") if has else "",
        row("fills", f"{tr['fills']:,}"),
        row("round trips", f"{tr['roundTrips']:,}"),
        row("win rate", f"{tr['winRate']*100:.1f}%") if tr["roundTrips"] else "",
        row("traded volume", f"{tr['volume']:,.0f}"),
        row("commission", f"{tr['commission']:,.2f}"),
        row("source", meta.get("source", "")),
        row("instruments", meta.get("instruments", "")),
    ])

    charts = ""
    if r["curve"]:
        charts += _chart("Equity", r["curve"])
        charts += _chart("Drawdown (%)", r["dd"], colour="#b03a2a")
    charts += _bars("P&amp;L by instrument", tail)

    ret = f"{eq['totalReturn']*100:+.3f}%" if has else "no equity"
    isOpen = " open" if openFirst else ""
    return (f"<details{isOpen}><summary>{r['name']}"
            f"<span class='meta'>{ret} &middot; {tr['fills']} fills</span></summary>"
            f"<table>{summary}</table>{charts}</details>")


def writeIndex(path: str, runs, sortKey: str = "date") -> str:
    if not runs:
        raise SystemExit("No runs to index.")

    head   = "".join(f"<th{' class=n' if num else ''}>{label}</th>"
                     for _, label, num in INDEX_COLUMNS)
    rows   = "".join(_indexRow(r) for r in runs)
    panels = "".join(_indexPanel(r, i == 0) for i, r in enumerate(runs))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Backtest runs</title>
<style>
body {{ font-family: ui-monospace, Menlo, Consolas, monospace; margin: 26px auto; max-width: 1000px;
        color: #222; background: #fafafa; }}
h2 {{ font-weight: 600; margin-bottom: 2px; }}
p.sub {{ color: #666; margin-top: 0; font-size: 12px; }}
table.runs {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-bottom: 30px; }}
table.runs th {{ text-align: left; border-bottom: 1px solid #999; padding: 5px 8px; }}
table.runs td {{ padding: 4px 8px; border-bottom: 1px solid #eee; }}
table.runs tr:hover {{ background: #f0f0f0; }}
td.n, th.n {{ text-align: right; }}
details {{ border-top: 1px solid #ddd; padding: 6px 0; }}
summary {{ cursor: pointer; font-weight: 600; font-size: 13px; padding: 4px 0; }}
summary span.meta {{ font-weight: 400; color: #666; margin-left: 14px; }}
details table {{ border-collapse: collapse; font-size: 13px; }}
details table td {{ padding: 3px 18px 3px 0; }}
h3 {{ font-weight: 600; font-size: 13px; margin: 22px 0 6px; }}
</style></head><body>
<h2>Backtest runs</h2>
<p class="sub">{len(runs)} run(s), sorted by {sortKey}</p>
<table class="runs"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>
{panels}
</body></html>"""

    with open(path, "w") as f:
        f.write(html)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", nargs="?", default=None, help="run directory (default: newest in results/)")
    ap.add_argument("--html", default=None, help="also write an HTML page to this path")
    ap.add_argument("--index", nargs="?", const=os.path.join("results", "index.html"),
                    default=None, help="build a browsable index of every run in results/")
    ap.add_argument("--sort", default="date",
                    help="index sort key: " + ", ".join(sorted(SORT_KEYS)))
    ap.add_argument("--asc", action="store_true", help="sort ascending")
    args = ap.parse_args()

    if args.index is not None:
        runs = sortRuns(collectRuns("results"), args.sort, args.asc)
        path = writeIndex(args.index, runs, args.sort)
        print(f"Indexed {len(runs)} run(s) sorted by {args.sort} -> {path}")
        return

    runDir = args.run
    if runDir is None:
        if not os.path.isdir("results"):
            raise SystemExit("No results/ directory. Run a backtest first.")
        runs = [os.path.join("results", d) for d in os.listdir("results")
                if os.path.isdir(os.path.join("results", d))]
        if not runs:
            raise SystemExit("No runs found in results/.")
        runDir = max(runs, key=os.path.getmtime)

    if not os.path.isdir(runDir):
        raise SystemExit(f"Not a run directory: {runDir}")

    meta, times, values, fills = loadRun(runDir)
    meta.setdefault("name", os.path.basename(os.path.normpath(runDir)))

    eq = equityStats(times, values)
    tr = tradeStats(fills, meta.get("marks", {}))
    printReport(meta, eq, tr)

    if args.html:
        path = writeHtml(args.html, meta, eq, tr, times, values)
        print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
