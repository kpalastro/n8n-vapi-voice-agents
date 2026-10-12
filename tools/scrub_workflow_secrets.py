#!/usr/bin/env python3
"""
Blank the provider credentials Vapi echoes into a pinned webhook payload.

Vapi's end-of-call report body repeats the dialled number's Twilio account SID and auth
token under `body.message.phoneNumber`. Pinning one of those payloads as test data writes
a live credential into the workflow JSON, and the JSON gets committed — so a token the
workflow itself never inlines still ends up in git, and GitHub's secret scanning push
protection then refuses the push.

This blanks those two fields and nothing else, by text substitution rather than by
re-serialising the document, so the file's formatting and key order are untouched. It is
idempotent: a field that is already empty is left alone.

Usage:
    python3 tools/scrub_workflow_secrets.py                  # every */*.json under the cwd
    python3 tools/scrub_workflow_secrets.py dentalClinic/VapiEndOfCall.json
"""

import re
import sys
from pathlib import Path

# The two fields Vapi copies out of the phone-number record. Matching on the field name
# is the point: the value is a secret we must not have to recognise.
PATTERN = re.compile(r'("twilio(?:AccountSid|AuthToken)"\s*:\s*")([^"]*)(")')


def scrub(path):
    """Blank the credential fields in one workflow. Returns the field names found."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    found = []

    def blank(match):
        if match.group(2):
            found.append(match.group(1).split('"')[1])
        return f"{match.group(1)}{match.group(3)}"

    scrubbed = PATTERN.sub(blank, text)
    if scrubbed != text:
        path.write_text(scrubbed, encoding="utf-8")
    return found


def main(argv):
    files = [Path(p) for p in argv if p.endswith(".json")]
    if not files:
        files = sorted(Path.cwd().glob("*/*.json"))
    if not files:
        print(f"no workflow JSON found in {Path.cwd()}", file=sys.stderr)
        return 1

    total = 0
    for path in files:
        found = scrub(path)
        if found:
            total += len(found)
            print(f"  {path}: blanked {len(found)} field(s) — {', '.join(sorted(set(found)))}")
    print(f"{total} field(s) blanked across {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
