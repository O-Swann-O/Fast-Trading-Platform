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