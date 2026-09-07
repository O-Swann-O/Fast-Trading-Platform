import os
import sys

import json
import argparse
import urllib.request
from html.parser import HTMLParser

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp500Symbols.json")


class _ConstituentParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.symbols   = []
        self._inTable  = False
        self._inBody   = False
        self._inRow    = False
        self._cellIdx  = -1
        self._capture  = False
        self._buf      = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") == "constituents":
            self._inTable = True
        elif self._inTable and tag == "tbody":
            self._inBody = True
        elif self._inBody and tag == "tr":
            self._inRow   = True
            self._cellIdx = -1
        elif self._inRow and tag in ("td", "th"):
            self._cellIdx += 1
            if self._cellIdx == 0 and tag == "td":
                self._capture = True
                self._buf = ""

    def handle_endtag(self, tag):
        if tag == "table" and self._inTable:
            self._inTable = False
            self._inBody  = False
        elif tag == "tr" and self._inRow:
            self._inRow = False
        elif tag in ("td", "th") and self._capture:
            self._capture = False
            sym = self._buf.strip()
            if sym:
                self.symbols.append(sym)

    def handle_data(self, data):
        if self._capture:
            self._buf += data


def toIbSymbol(symbol: str) -> str:
    return symbol.replace(".", " ")


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", errors="replace")

    parser = _ConstituentParser()
    parser.feed(html)
    symbols = parser.symbols

    if len(symbols) < 400:
        raise RuntimeError(f"Parsed only {len(symbols)} symbols; page layout may have changed.")
    return symbols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    symbols = fetch()
    ib      = [toIbSymbol(s) for s in symbols]

    with open(args.out, "w") as f:
        json.dump({"wikipedia": symbols, "ib": ib}, f, indent=1)

    renamed = [(w, i) for w, i in zip(symbols, ib) if w != i]
    print(f"{len(symbols)} constituents written to {args.out}")
    if renamed:
        print(f"rewritten for IB: {', '.join(f'{w} -> {i}' for w, i in renamed)}")


if __name__ == "__main__":
    main()