#!/usr/bin/env python3
"""
Redact personal data from an exported call log before it is committed.

Masks the `phone` column (keeping the country code and last two digits, so the
column still reads as a phone number) and blanks `recording_url`, because Vapi
recording links are typically unauthenticated and anyone holding one can listen
to the call.

Everything else — names, transcripts, grades, costs, word counts — is preserved,
so the reporting figures are unaffected.

The edit is done by targeted replacement in the raw text rather than by parsing
and re-writing the CSV, so the original line endings, quoting and byte layout are
preserved exactly. Only the redacted substrings change.

Usage:
    python3 tools/redact_call_logs.py realestateOutboundCall/RealestateOutboundCallLogs.csv
    python3 tools/redact_call_logs.py <input.csv> --out <clean.csv>   # no backup needed

Without --out the file is edited in place and the original is copied to a
timestamped .original.bak beside it.
"""

import argparse
import csv
import re
import shutil
import sys
import time
from pathlib import Path

PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,20}$")


def mask_phone(value):
    """+61419673611 -> +614XXXXXX11. Keeps country code and last two digits."""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 6:
        return "[redacted]"
    return f"+{digits[:3]}{'X' * (len(digits) - 5)}{digits[-2:]}"


def collect_values(path):
    """Return the exact phone and recording_url strings present in the file."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    phones, urls = set(), set()
    for row in rows:
        phone = (row.get("phone") or "").strip()
        if phone and PHONE_RE.match(phone):
            phones.add(phone)
        url = (row.get("recording_url") or "").strip()
        if url:
            urls.add(url)
    return phones, urls, len(rows)


def redact(path, out_path):
    phones, urls, n_rows = collect_values(path)
    text = path.read_text(encoding="utf-8-sig")

    # longest first, so a shorter value can never truncate a longer match
    replacements = []
    for phone in sorted(phones, key=len, reverse=True):
        replacements.append((phone, mask_phone(phone)))
    for url in sorted(urls, key=len, reverse=True):
        replacements.append((url, ""))

    counts = {}
    for old, new in replacements:
        counts[old] = text.count(old)

    if out_path is None:
        backup = path.with_suffix(path.suffix + f".original.bak")
        shutil.copy2(path, backup)
        print(f"backup  -> {backup}")

    for old, new in replacements:
        text = text.replace(old, new)

    target = out_path or path
    target.write_text(text, encoding="utf-8")

    print(f"rows    -> {n_rows}")
    print(f"phones  -> {len(phones)} masked")
    for old, new in replacements:
        if old in phones:
            print(f"           {old} -> {new}  ({counts[old]} occurrence(s))")
    print(f"records -> {len(urls)} recording URLs blanked")
    print(f"written -> {target}")

    # verify nothing survived
    remaining = target.read_text(encoding="utf-8-sig")
    leaked = [p for p in phones if p in remaining] + [u for u in urls if u in remaining]
    if leaked:
        print(f"ERROR: {len(leaked)} value(s) still present after redaction", file=sys.stderr)
        return 1
    print("verify  -> no original phone number or recording URL remains")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("csv", type=Path, help="call log to redact")
    ap.add_argument("--out", type=Path, default=None,
                    help="write here instead of editing in place (no backup made)")
    args = ap.parse_args()
    if not args.csv.exists():
        print(f"no such file: {args.csv}", file=sys.stderr)
        return 1
    return redact(args.csv, args.out)


if __name__ == "__main__":
    sys.exit(main())
