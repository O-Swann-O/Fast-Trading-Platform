import os
import csv
import json
import array
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

FILLS_FILE  = "fills.csv"
EQUITY_FILE = "equity.parquet"
META_FILE   = "meta.json"

FILL_HEADER = ["time", "conId", "symbol", "action", "qty", "price", "commission", "position", "equity"]


def _epoch(ts) -> int:
    if isinstance(ts, (int, float)):
        return int(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp())


class Recorder:

    def __init__(self, outDir: str, flushEachFill: bool = False):
        self.outDir  = outDir
        self._flush  = flushEachFill
        os.makedirs(outDir, exist_ok=True)

        self._fillFile   = open(os.path.join(outDir, FILLS_FILE), "w", newline="")
        self._fillWriter = csv.writer(self._fillFile)
        self._fillWriter.writerow(FILL_HEADER)
        if self._flush:
            self._fillFile.flush()

        self._times  = array.array("q")
        self._values = array.array("d")
        self.fills   = 0

    def fill(self, ts, conId, symbol, action, qty, price, position, equity,
             commission=0.0) -> None:
        self._fillWriter.writerow([_epoch(ts), int(conId), symbol, action,
                                   int(qty), f"{price:.8f}", f"{commission:.4f}",
                                   int(position), f"{equity:.4f}"])
        self.fills += 1
        if self._flush:
            self._fillFile.flush()

    def equity(self, ts, value: float) -> None:
        self._times.append(_epoch(ts))
        self._values.append(float(value))

    def _writeEquity(self) -> None:
        if not self._times:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.table({
            "time":   pa.array(self._times, type=pa.int64()),
            "equity": pa.array(self._values, type=pa.float64()),
        })
        pq.write_table(table, os.path.join(self.outDir, EQUITY_FILE))

    def finish(self, meta: dict) -> str:
        try:
            self._fillFile.flush()
            self._fillFile.close()
        except Exception as e:
            log.error("Could not close fill log: %s", e)

        try:
            self._writeEquity()
        except Exception as e:
            log.error("Could not write equity series: %s", e)

        meta = dict(meta)
        meta.setdefault("savedAt", datetime.now(timezone.utc).isoformat())
        meta["fills"]         = self.fills
        meta["equitySamples"] = len(self._times)
        with open(os.path.join(self.outDir, META_FILE), "w") as f:
            json.dump(meta, f, indent=1, default=str)

        log.info("Run saved to %s (%d fills, %d equity samples).",
                 self.outDir, self.fills, len(self._times))
        return self.outDir
