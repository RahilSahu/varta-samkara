#!/usr/bin/env python3
"""Fetch latest IBJA bullion rates and update markets_rates.json.

Runs hourly via GitHub Actions. Exits 0 without writing on any failure
or failed sanity check, so the repo never gets garbage data.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

API_URL = "https://ibja-api.vercel.app/history"
OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "markets_rates.json")


def log(msg):
    print(f"[ibja-rates] {msg}", flush=True)


def pick(entries):
    am = {e["date"]: e for e in (entries.get("am") or []) if e.get("date")}
    pm = {e["date"]: e for e in (entries.get("pm") or []) if e.get("date")}
    try:
        dates = sorted(set(am) | set(pm),
                       key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
    except Exception:
        return None, None, None
    if not dates:
        return None, None, None
    latest = dates[-1]
    if latest in pm:
        return latest, pm[latest], "PM"
    return latest, am[latest], "AM"


def main():
    try:
        req = urllib.request.Request(API_URL, headers={
            "User-Agent": "Mozilla/5.0 (varta-samkara rates updater)",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except Exception as e:
        log(f"fetch failed: {e}; leaving markets_rates.json untouched")
        return 0

    date, entry, session = pick(data)
    if not entry:
        log("no usable date entries in API response; leaving file untouched")
        return 0

    try:
        g999 = round(float(entry["gold_999"]) / 10, 2)
        g916 = round(float(entry["gold_916"]) / 10, 2)
        s999 = int(float(entry["silver_999"]))
    except Exception as e:
        log(f"parse failed: {e}; leaving file untouched")
        return 0

    if not (10000 < g999 < 25000 and 9000 < g916 < 23000
            and 150000 < s999 < 400000):
        log(f"sanity check failed: g999={g999} g916={g916} s999={s999}; "
            "leaving file untouched")
        return 0

    rates = {
        "date": date,
        "session": session,
        "gold_999_g": g999,
        "gold_916_g": g916,
        "silver_999_kg": s999,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    old = None
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = None

    def sig(r):
        return (r.get("date"), r.get("session"), r.get("gold_999_g"),
                r.get("gold_916_g"), r.get("silver_999_kg")) if r else None

    if sig(old) == sig(rates):
        log("rates unchanged; nothing to write")
        return 0

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(rates, f, ensure_ascii=False, indent=1)
        f.write("\n")
    log(f"wrote {OUT_FILE}: {date} {session} "
        f"g999={g999}/g g916={g916}/g s999={s999}/kg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
