"""Resumable, polite puller for Kentucky State Police collision extracts (crashinformationky.org).

The public Advanced Search runs one query per request (~1 min of server time whatever the size)
and returns a zip of comma-delimited tables (Incident, Vehicle, Person, UnitFactor, ...).
Pre-built yearly extracts exist only for 2010-2015 (/Home/Extracts); 2016+ needs this route.

Flow per chunk (fresh session each time):
  GET  /AdvancedSearch                -> session cookie + anti-forgery token
  POST /AdvancedSearch/GetModel       -> EasyQuery model id/name
  POST /AdvancedSearch/SetExtractType -> stores query JSON + file type (COM = CSV) in the session
  GET  /AdvancedSearch/GenerateExtract-> application/zip

Gotcha: the server parses date constants as dd/mm/yyyy.

Usage:
  python -I kycrash_pull.py OUT --counties PIKE,FLOYD --years 2016-2025 [--chunk year|month]
         [--group per-county|list] [--window 01:00-06:00] [--pause 30] [--max-chunks N]
"""
import argparse
import calendar
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

BASE = "http://crashinformationky.org"  # port 443 does not answer
UA = "Mozilla/5.0"
COUNTY_EDITOR = "SQLLVE560"


def session_and_token():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(f"{BASE}/AdvancedSearch", timeout=300)
    r.raise_for_status()
    token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', r.text).group(1)
    m = s.post(f"{BASE}/AdvancedSearch/GetModel",
               data={"modelId": "", "modelName": "", "__RequestVerificationToken": token}, timeout=300)
    m.raise_for_status()
    model = m.json()
    return s, token, model["id"], model["name"]


def county_codes(s, token):
    r = s.post(f"{BASE}/AdvancedSearch/GetList",
               data={"listName": "SQL", "editorId": COUNTY_EDITOR, "__RequestVerificationToken": token}, timeout=300)
    r.raise_for_status()
    return {c["text"].strip().upper(): c["id"] for c in r.json()}


def const(value, text=None, kind="Scalar", dtype="String"):
    return {"typeName": "CONST", "dataType": dtype, "kind": kind, "value": value, "text": text or value}


def build_query(model_id, model_name, d0, d1, codes, names):
    fmt = "%d/%m/%Y"  # server reads dd/mm/yyyy
    county = ({"typeName": "SMPL", "enabled": True, "operatorID": "Equal",
               "expressions": [{"typeName": "ENTATTR", "id": "Location.CountyCde"}, const(codes[0], names[0])]}
              if len(codes) == 1 else
              {"typeName": "SMPL", "enabled": True, "operatorID": "InList",
               "expressions": [{"typeName": "ENTATTR", "id": "Location.CountyCde"},
                               const(",".join(codes), ", ".join(names), kind="List")]})
    return json.dumps({
        "id": "", "name": "", "columns": [], "justsorted": [], "modelId": model_id, "modelName": model_name,
        "root": {"linkType": "All", "enabled": True, "conditions": [
            {"typeName": "SMPL", "enabled": True, "operatorID": "DateFromTo",
             "expressions": [{"typeName": "ENTATTR", "id": "Incident.IncidentDte"},
                             const(d0.strftime(fmt), dtype="Date"), const(d1.strftime(fmt), dtype="Date")]},
            county]},
    })


def chunks(years, how):
    for y in years:
        if how == "year":
            yield f"{y}", dt.date(y, 1, 1), dt.date(y, 12, 31)
        else:
            for mth in range(1, 13):
                yield f"{y}-{mth:02d}", dt.date(y, mth, 1), dt.date(y, mth, calendar.monthrange(y, mth)[1])


def check_zip(blob, d0, d1, names):
    z = zipfile.ZipFile(io.BytesIO(blob))
    rows = list(csv.DictReader(io.TextIOWrapper(z.open("Incident.txt"), encoding="utf-8", errors="replace")))
    dates = [dt.datetime.strptime(r["CollisionDate"].split()[0], "%m/%d/%Y").date() for r in rows if r["CollisionDate"]]
    out_of_range = sum(1 for d in dates if not d0 <= d <= d1)
    wrong_county = sum(1 for r in rows if r["County"].strip().upper() not in names)
    return {"tables": [i.filename for i in z.infolist()], "incidents": len(rows),
            "out_of_range": out_of_range, "wrong_county": wrong_county,
            "first": min(dates).isoformat() if dates else None, "last": max(dates).isoformat() if dates else None}


def chunk_key(counties, label):
    return f"{'+'.join(counties) if len(counties) < 8 else f'{len(counties)}counties'}_{label}"


def in_window(window):
    if not window:
        return True
    a, b = (dt.time.fromisoformat(x) for x in window.split("-"))
    now = dt.datetime.now().time()
    return a <= now < b if a < b else (now >= a or now < b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--counties", required=True, help="comma-separated county names, or ALL")
    ap.add_argument("--years", required=True, help="e.g. 2016-2025 or 2024")
    ap.add_argument("--chunk", choices=["year", "month"], default="year")
    ap.add_argument("--group", choices=["per-county", "list"], default="per-county",
                    help="one request per county, or one InList request for all counties")
    ap.add_argument("--window", help="only start requests inside HH:MM-HH:MM local time")
    ap.add_argument("--pause", type=float, default=30, help="seconds between requests")
    ap.add_argument("--max-chunks", type=int, default=0)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    raw = a.out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = a.out / "manifest.jsonl"
    done = set()
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("status") == "ok":
                done.add(rec["key"])

    y0, _, y1 = a.years.partition("-")
    years = range(int(y0), int(y1 or y0) + 1)
    s, token, mid, mname = session_and_token()
    codes = county_codes(s, token)
    names = sorted(codes) if a.counties.upper() == "ALL" else [c.strip().upper() for c in a.counties.split(",")]
    missing = [n for n in names if n not in codes]
    if missing:
        sys.exit(f"unknown counties: {missing}")
    groups = [[n] for n in names] if a.group == "per-county" else [names]

    todo = [(g, c) for c in chunks(years, a.chunk) for g in groups]
    todo = [(g, c) for g, c in todo if chunk_key(g, c[0]) not in done]
    print(f"{len(todo)} chunks to pull ({len(done)} already done)", flush=True)

    pulled = 0
    for g, (label, d0, d1) in todo:
        if a.max_chunks and pulled >= a.max_chunks:
            break
        if not in_window(a.window):
            print(f"outside window {a.window}; stopping", flush=True)
            break
        key = chunk_key(g, label)
        for attempt in range(1, a.retries + 1):
            t = time.time()
            rec = {"key": key, "counties": g, "from": d0.isoformat(), "to": d1.isoformat(),
                   "attempt": attempt, "started": dt.datetime.now().isoformat(timespec="seconds")}
            try:
                s, token, mid, mname = session_and_token()
                q = build_query(mid, mname, d0, d1, [codes[n] for n in g], g)
                r = s.post(f"{BASE}/AdvancedSearch/SetExtractType",
                           data={"data": "COM", "json": q, "__RequestVerificationToken": token}, timeout=300)
                r.raise_for_status()
                r = s.get(f"{BASE}/AdvancedSearch/GenerateExtract", timeout=3600)
                r.raise_for_status()
                if "zip" not in (r.headers.get("Content-Type") or ""):
                    raise RuntimeError(f"not a zip: {r.headers.get('Content-Type')} {r.text[:200]!r}")
                info = check_zip(r.content, d0, d1, set(g))
                if info["out_of_range"] or info["wrong_county"]:
                    raise RuntimeError(f"filter mismatch: {info}")
                dest = raw / f"{key}.zip"
                dest.write_bytes(r.content)
                rec.update(status="ok", seconds=round(time.time() - t, 1), bytes=len(r.content),
                           sha256=hashlib.sha256(r.content).hexdigest(), file=dest.name, **info)
            except Exception as e:  # noqa: BLE001 - record and retry
                rec.update(status="error", seconds=round(time.time() - t, 1), error=f"{type(e).__name__}: {e}"[:500])
            with manifest.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{key}: {rec['status']} {rec.get('incidents', '')} in {rec['seconds']}s {rec.get('error', '')}",
                  flush=True)
            if rec["status"] == "ok":
                break
            time.sleep(min(600, a.pause * 2 ** attempt))
        pulled += 1
        time.sleep(a.pause)


if __name__ == "__main__":
    main()
