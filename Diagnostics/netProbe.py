"""
Every request to Dukascopy takes ~20 seconds, successes included. Find out which
phase is stalling.

A healthy fetch of a 3 KB file is: DNS a few ms, TCP one round trip, TLS two, then
the bytes. If one phase eats 20 seconds, that phase is the problem, and each points
somewhere different:

  DNS slow            -> resolver problem; try 1.1.1.1 or 8.8.8.8
  TCP connect slow    -> firewall silently dropping SYN, or dead IPv6 route
  TLS handshake slow  -> inspecting proxy or antivirus MITM on the connection
  transfer slow       -> genuine throttling of the download itself
  IPv6 slow, IPv4 ok  -> broken IPv6; force IPv4

    python Diagnostics/netProbe.py
"""
import ssl
import time
import socket
import argparse
import statistics

HOST, PORT = "datafeed.dukascopy.com", 443
PATH       = "/datafeed/CADCHF/2024/03/01/10h_ticks.bi5"   # known to return 200, ~3 kB

ap = argparse.ArgumentParser()
ap.add_argument("--ip", action="append", default=[],
                help="also test this server IP directly, bypassing DNS. Repeatable. "
                     "Use it to try an IP that was fast on another network.")
ap.add_argument("--rounds", type=int, default=3)
args   = ap.parse_args()
ROUNDS = args.rounds


def resolve(family, label):
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(HOST, PORT, family, socket.SOCK_STREAM)
    except Exception as e:
        print(f"  DNS {label:6s} FAILED after {time.perf_counter()-t0:5.2f}s: {type(e).__name__}: {e}")
        return []
    el   = time.perf_counter() - t0
    addrs = sorted({i[4][0] for i in infos})
    print(f"  DNS {label:6s} {el:5.2f}s  -> {', '.join(addrs)}")
    return infos


def timeOne(info, label):
    fam, _, _, _, sockaddr = info
    ip = sockaddr[0]
    phases = {}
    s = None
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(30)
        t0 = time.perf_counter()
        s.connect(sockaddr)
        phases["tcp"] = time.perf_counter() - t0

        ctx = ssl.create_default_context()
        t0  = time.perf_counter()
        ss  = ctx.wrap_socket(s, server_hostname=HOST)
        phases["tls"] = time.perf_counter() - t0

        req = (f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\n"
               f"User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n").encode()
        t0 = time.perf_counter()
        ss.sendall(req)
        first = ss.recv(4096)
        phases["firstByte"] = time.perf_counter() - t0

        t0   = time.perf_counter()
        body = first
        while True:
            chunk = ss.recv(65536)
            if not chunk:
                break
            body += chunk
        phases["transfer"] = time.perf_counter() - t0

        status = body.split(b"\r\n", 1)[0].decode(errors="replace")
        size   = len(body)
        ss.close()
        total = sum(phases.values())
        print(f"  {label:22s} {ip:26s} tcp {phases['tcp']:5.2f}  tls {phases['tls']:5.2f}  "
              f"1st {phases['firstByte']:5.2f}  xfer {phases['transfer']:5.2f}  "
              f"= {total:5.2f}s  {status}  {size:,}B")
        return total, phases
    except Exception as e:
        el = sum(phases.values())
        done = "  ".join(f"{k} {v:.2f}" for k, v in phases.items())
        print(f"  {label:22s} {ip:26s} FAILED after {el:5.2f}s ({done or 'nothing completed'}): "
              f"{type(e).__name__}: {str(e)[:40]}")
        return None, phases
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass


print(f"target: https://{HOST}{PATH}\n")
print("NAME RESOLUTION")
v4 = resolve(socket.AF_INET,   "IPv4")
v6 = resolve(socket.AF_INET6,  "IPv6")
both = resolve(socket.AF_UNSPEC, "either")

targets = []
seen    = set()
for infos, label in ((v4, "IPv4"), (v6, "IPv6")):
    for info in infos:                       # every resolved address, not just the first
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            targets.append((info, f"{label} {ip}"))
for ip in args.ip:                           # explicit IPs bypass DNS entirely
    if ip in seen:
        continue
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    targets.append(((fam, None, None, None, (ip, PORT)), f"forced {ip}"))

print(f"\nCONNECTION BREAKDOWN ({ROUNDS} rounds, {len(targets)} target(s))")
results  = {}
firstByte = {}
for r in range(1, ROUNDS + 1):
    print(f" round {r}")
    for info, label in targets:
        total, phases = timeOne(info, label)
        results.setdefault(label, []).append(total)
        if "firstByte" in phases:
            firstByte.setdefault(label, []).append(phases["firstByte"])
    time.sleep(1)

print("\nVERDICT")
print("  first-byte time is the only phase that shows how the server treats you;")
print("  tcp and tls reflect your own network path.\n")
for label, times in results.items():
    ok = [t for t in times if t is not None]
    fb = firstByte.get(label, [])
    fbs = f", median first-byte {statistics.median(fb):6.2f}s" if fb else ""
    if not ok:
        print(f"  {label:22s} 0/{len(times)} succeeded{fbs}")
    else:
        print(f"  {label:22s} {len(ok)}/{len(times)} succeeded, "
              f"median total {statistics.median(ok):6.2f}s{fbs}")

fast = [l for l, fb in firstByte.items() if fb and statistics.median(fb) < 1.0]
slow = [l for l, fb in firstByte.items() if fb and statistics.median(fb) >= 1.0]
print()
if fast and slow:
    print(f"  Some servers answer instantly ({', '.join(fast)}) and others stall")
    print(f"  ({', '.join(slow)}). The throttling is per-server, so pin a fast IP.")
elif slow and not fast:
    print("  Every server stalls before the first byte. The limit follows you, not the")
    print("  server, so it is tied to this IP address. Wait it out or fetch elsewhere.")
elif fast:
    print("  Every server answers promptly. Nothing is throttling you right now.")