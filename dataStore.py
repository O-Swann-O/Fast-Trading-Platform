import os
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema([
    ("time",  pa.timestamp("us")),
    ("conId", pa.int64()),
    ("bid",   pa.float64()),
    ("ask",   pa.float64()),
])


def _day_path(root, symbol, day):
    d = os.path.join(root, symbol)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{day}.parquet")


def write_day(root, symbol, day, rows):
    if not rows:
        return None
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    path  = _day_path(root, symbol, day)
    pq.write_table(table, path)
    return path


def _glob(root):
    return os.path.join(root, "*", "*.parquet").replace("'", "''")


def query(root, conIds, start, end):
    con = duckdb.connect()
    placeholders = ",".join("?" for _ in conIds)
    sql = f"""
        SELECT time, conId, bid, ask
        FROM read_parquet('{_glob(root)}')
        WHERE conId IN ({placeholders})
          AND time BETWEEN ? AND ?
        ORDER BY time
    """
    return con.execute(sql, [*conIds, start, end])


def export_csv(root, conIds, start, end, out_path):
    con = duckdb.connect()
    placeholders = ",".join("?" for _ in conIds)
    sql = f"""
        COPY (
          SELECT time, conId, bid, ask
          FROM read_parquet('{_glob(root)}')
          WHERE conId IN ({placeholders})
            AND time BETWEEN ? AND ?
          ORDER BY time
        ) TO '{out_path.replace("'", "''")}' (HEADER, DELIMITER ',')
    """
    con.execute(sql, [*conIds, start, end])
    return out_path