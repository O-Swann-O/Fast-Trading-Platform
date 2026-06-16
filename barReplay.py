import csv
from collections import namedtuple
from datetime import datetime

Tick = namedtuple("Tick", ["ts", "conId", "bid", "ask"])


def load_csv(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        has_quote = "bid" in fields and "ask" in fields
        for row in reader:
            ts    = datetime.fromisoformat(row["time"])
            conId = int(row["conId"])
            if has_quote:
                bid = float(row["bid"])
                ask = float(row["ask"])
            else:
                px  = float(row["price"])
                bid = ask = px
            yield Tick(ts, conId, bid, ask)


def load_duckdb(root, conIds, start, end):
    import dataStore
    rel = dataStore.query(root, conIds, start, end)
    while True:
        batch = rel.fetchmany(10000)
        if not batch:
            break
        for ts, conId, bid, ask in batch:
            yield Tick(ts, int(conId), float(bid), float(ask))