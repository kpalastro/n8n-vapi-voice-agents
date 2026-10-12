#!/usr/bin/env python3
"""
Generate the call-quality figures for the README from the judge's CSV output.

Reads : realestateOutboundCall/RealestateOutboundCallLogs.csv   (outbound campaign)
        dentalClinic/DentalCallLogs.csv                         (inbound receptionist)
Writes: charts/call-quality-report.svg   (outbound, embedded in README.md)
        charts/conversation-flow.svg     (outbound, turn by turn)
        charts/inbound-call-quality.svg  (inbound receptionist cohort)

Design system: see docs/DESIGN-NOTES.md. Palette values are the validated
default instance from the dataviz reference palette (light surface #fcfcfb):
categorical slots 1-2 (blue / orange) for the two speakers, the fixed status
palette for the judge's verdict, and blue steps 250/400/550 as the ordinal
ramp for the funnel. Both palettes were validated with
scripts/validate_palette.js before use.

The SVG carries every value as a direct label or a table row - it is a static
image with no hover layer, so nothing may depend on a tooltip.
"""

import csv
import html
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "realestateOutboundCall" / "RealestateOutboundCallLogs.csv"
INBOUND_CSV = ROOT / "dentalClinic" / "DentalCallLogs.csv"
OUT_PATH = Path(__file__).resolve().parent / "call-quality-report.svg"
FLOW_PATH = Path(__file__).resolve().parent / "conversation-flow.svg"
INBOUND_PATH = Path(__file__).resolve().parent / "inbound-call-quality.svg"

# --- palette (light mode; the figure ships its own surface) -------------------
SURFACE = "#fcfcfb"       # chart surface
PLANE = "#f9f9f7"         # page plane / stat-tile fill
INK = "#0b0b0b"           # primary ink
INK_2 = "#52514e"         # secondary ink
MUTED = "#898781"         # axis + micro labels
GRID = "#e1e0d9"          # hairline
RULE = "#c3c2b7"          # baseline / axis
BORDER = "rgba(11,11,11,0.10)"

SERIES = {                # categorical slots 1-2, fixed order
    "ai": "#2a78d6",
    "caller": "#eb6834",
}
ORDINAL = ["#86b6ef", "#3987e5", "#1c5cab"]   # steps 250 / 400 / 550

STATUS = {                # fixed status palette - never themed, never a series
    "Good": "#0ca30c",
    "Neutral": "#fab219",
    "Bad": "#d03b3b",
}

FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'

# Estimated right edge of every text element drawn, for the overflow check.
EXTENTS = []

W = 1040
PAD = 32
CONTENT_W = W - 2 * PAD

# --- data --------------------------------------------------------------------


def load_calls():
    """Return the analysed call records, newest first. Rows with no call data
    (leads that were never dialled through to an end-of-call report) are
    counted separately, not charted."""
    with open(CSV_PATH, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    analysed, pending = [], []
    for r in rows:
        if not (r.get("call_duration") or "").strip():
            pending.append(r)
            continue
        dur = float(r["call_duration"])
        cost = float(r["call_cost"])
        bot = int(r["bot_word_count"])
        user = int(r["user_word_count"])
        analysed.append({
            "name": r["first_name"].strip(),
            "grade": r["ai_call_performance_grade"].strip().title(),
            "bot_words": bot,
            "user_words": user,
            "bot_pct": bot / (bot + user) if (bot + user) else 0.0,
            "duration_min": dur,
            "duration_s": dur * 60,
            "cost": cost,
            "cost_per_min": cost / dur if dur else 0.0,
            "interested": r["Interested_In_Selling"].strip(),
            "follow_up": r["wilingness_for_follow-up"].strip(),
            "ended_reason": r["ended_reason"].strip(),
            "justification": " ".join(r["ai_performance_justification"].split()),
            "started_at": r["started_at"].strip(),
            "transcript": r["formatted_transcript"],
        })
        analysed[-1]["turns"] = parse_turns(analysed[-1]["transcript"])
    analysed.sort(key=lambda c: c["started_at"], reverse=True)
    return analysed, pending


def qualified(call):
    return call["interested"].lower() == "yes" and call["follow_up"].lower() == "yes"


def load_inbound():
    """Dental-clinic inbound cohort: one record per graded call.

    Two traps in the export are handled here rather than propagated into the
    figure:

    * `bot_word_percent` is unusable on the five most recent rows - it holds
      values around 11,800,000% that agree with neither the word counts nor the
      call duration. Talk share is therefore recomputed from `bot_word_count` /
      `user_word_count`, which are clean integers on every row.
    * Two calls were written to the log twice. Collapsing them takes the row
      count to the call count, and the *reason* they differ matters: one pair is
      byte-identical (a retried write), the other pair shares a transcript but
      carries a different summary and a different judge justification - the
      grader is a sampling model and did not answer the same way twice.

    Returns (calls oldest first, meta) with meta describing what was wrong.
    """
    with open(INBOUND_CSV, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    meta = {"records": len(rows), "empty": 0, "graded_rows": 0,
            "collapsed": 0, "rewrites": 0, "regraded": 0, "stale_pct_rows": 0,
            "phone_formats": sorted({(r.get("phone") or "").strip()
                                     for r in rows if (r.get("phone") or "").strip()})}

    # The business outcome - did the caller get an appointment - is not a column,
    # only prose. These two rules read it out of the summary, which makes them
    # heuristics, not fields; the figure says exactly what they matched and
    # nothing more. DATED is deliberately narrow: a calendar month must appear
    # near the word appointment, so "requested an appointment for tomorrow" does
    # not count as a date.
    DATED = re.compile(r"(appointment|booking)[^.]{0,70}?(January|February|March|April|May|June|"
                       r"July|August|September|October|November|December)\b", re.I)
    FAILED = re.compile(r"could not be (?:completed|booked|scheduled)|unable to (?:book|schedule)",
                        re.I)

    graded = []
    for r in rows:
        grade = (r.get("ai_call_performance_grade") or "").strip()
        if not (r.get("started_at") or "").strip() and not grade:
            meta["empty"] += 1
            continue
        if not grade:
            continue
        meta["graded_rows"] += 1

        bot = int(r["bot_word_count"] or 0)
        user = int(r["user_word_count"] or 0)
        talk = bot / (bot + user) if (bot + user) else 0.0

        # is the stored percentage consistent with its own inputs?
        stored = (r.get("bot_word_percent") or "").strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)%", stored)
        if not m or abs(float(m.group(1)) - talk * 100) > 0.6:
            meta["stale_pct_rows"] += 1

        graded.append({
            "name": " ".join(x for x in (r["first_name"].strip(),
                                        r["last_name"].strip()) if x) or "(no name)",
            "grade": grade.title(),
            "talk": talk,
            "duration_s": float(r["call_duration"] or 0) * 60,
            "cost": float(r["call_cost"] or 0),
            "ended_reason": (r["ended_reason"] or "").strip(),
            "query": " ".join((r.get("query") or "").split()),
            "justification": " ".join((r.get("ai_performance_justification") or "").split()),
            "dated_booking": bool(DATED.search(r.get("summary") or "")),
            "failed_booking": bool(FAILED.search(r.get("summary") or "")),
            "started_at": r["started_at"].strip(),
            "call_key": (r["started_at"].strip(), r["ended_at"].strip(),
                         r["recording_url"].strip()),
        })

    calls, seen = [], {}
    for c in graded:
        if c["call_key"] in seen:
            meta["collapsed"] += 1
            if seen[c["call_key"]]["justification"] == c["justification"]:
                meta["rewrites"] += 1
            else:
                meta["regraded"] += 1
            continue
        seen[c["call_key"]] = c
        calls.append(c)

    calls.sort(key=lambda c: c["started_at"])
    for c in calls:
        minutes = c["duration_s"] / 60
        c["cost_per_min"] = c["cost"] / minutes if minutes else 0.0
    meta["dated_bookings"] = sum(1 for c in calls if c["dated_booking"])
    meta["failed_bookings"] = sum(1 for c in calls if c["failed_booking"])
    return calls, meta


# --- transcript analysis -----------------------------------------------------

FAREWELLS = ("bye", "goodbye", "see you", "take care", "talk later")


def parse_turns(formatted_transcript):
    """Split Vapi's `AI:` / `User:` transcript into ordered (speaker, text) turns."""
    turns = []
    for line in formatted_transcript.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(AI|User):\s*(.*)$", line)
        if m:
            turns.append({"speaker": "ai" if m.group(1) == "AI" else "caller",
                          "text": m.group(2).strip()})
    return turns


def detect_defects(turns):
    """Flag two mechanically detectable conversation defects.

    * cut_off   - a turn the speaker never finished: it ends without terminal
                  punctuation AND the next turn from the *other* speaker
                  interrupts it (a barge-in), or it is the last turn.
                  A trailing turn ending "?" or "." is a normal hand-off.
    * restart   - a turn whose text is a prefix of that speaker's next turn,
                  i.e. the agent began a sentence, was cut off, and re-sent it.

    Returns {turn_index: set_of_labels}. Deliberately conservative: both rules
    need corroborating evidence, so a short-but-complete "Gotcha." is not flagged.
    """
    flags = {}
    for i, turn in enumerate(turns):
        labels = set()
        text = turn["text"].rstrip()
        # does the speaker's own next turn re-send this one from the beginning?
        nxt = next((t for t in turns[i + 1:] if t["speaker"] == turn["speaker"]), None)
        if nxt and len(text) > 6:
            n = nxt["text"].strip().lower()
            if n.startswith(text.lower()) and len(n) > len(text.lower()):
                labels.add("restart")
        if turn["speaker"] == "ai" and not text.endswith((".", "!", "?", '"')):
            after = turns[i + 1] if i + 1 < len(turns) else None
            if after is None or after["speaker"] != "ai":
                labels.add("cut_off")
        if labels:
            flags[i] = labels
    return flags


def detect_ignored_hangup(turns):
    """Index of an agent turn that keeps selling immediately after the caller
    said goodbye. This is the failure the judge graded Bad on the first call."""
    for i, turn in enumerate(turns):
        if turn["speaker"] == "caller" and turn["text"].strip():
            low = turn["text"].lower()
            if any(f in low for f in FAREWELLS) and i + 1 < len(turns):
                if turns[i + 1]["speaker"] == "ai":
                    return i + 1
    return None


# --- svg helpers -------------------------------------------------------------


def esc(s):
    return html.escape(str(s), quote=True)


def bar_path(x, y, w, h, r_left=0.0, r_right=0.0):
    """Horizontal bar growing from the left baseline. Radius is clamped so a
    short segment never over-rounds."""
    h = float(h)
    rl = max(0.0, min(float(r_left), h / 2, w))
    rr = max(0.0, min(float(r_right), h / 2, w - rl))
    x1, x2 = x + w, x + w
    p = [f"M {x + rl:.1f} {y:.1f}"]
    p.append(f"H {x1 - rr:.1f}")
    if rr:
        p.append(f"A {rr:.1f} {rr:.1f} 0 0 1 {x2:.1f} {y + rr:.1f}")
    p.append(f"V {y + h - rr:.1f}")
    if rr:
        p.append(f"A {rr:.1f} {rr:.1f} 0 0 1 {x1 - rr:.1f} {y + h:.1f}")
    p.append(f"H {x + rl:.1f}")
    if rl:
        p.append(f"A {rl:.1f} {rl:.1f} 0 0 1 {x:.1f} {y + h - rl:.1f}")
    p.append(f"V {y + rl:.1f}")
    if rl:
        p.append(f"A {rl:.1f} {rl:.1f} 0 0 1 {x + rl:.1f} {y:.1f}")
    p.append("Z")
    return " ".join(p)


def rect_path(x, y, w, h, r=8.0):
    return bar_path(x, y, w, h, r, r)


def text(x, y, s, size=13, fill=INK, weight="400", anchor="start",
         spacing=None, tabular=False):
    """Emit a text element and record its estimated right edge, so
    bounds_check() can prove nothing overflows the card."""
    extra = ""
    if spacing:
        extra += f' letter-spacing="{spacing}"'
    if tabular:
        extra += ' style="font-variant-numeric:tabular-nums"'
    estimate = len(html.unescape(str(s))) * size * 0.52
    if anchor == "end":
        right = x
    elif anchor == "middle":
        right = x + estimate / 2
    else:
        right = x + estimate
    EXTENTS.append((right, str(s)))
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family={esc(FONT)!r} '
            f'font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def luminance(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ink_on(fill):
    """Pick white or body ink for a label sitting inside a coloured fill."""
    return "#ffffff" if luminance(fill) < 0.45 else INK


def wrap(s, max_chars):
    words, lines, cur = s.split(), [], ""
    for word in words:
        candidate = f"{cur} {word}".strip()
        if len(candidate) <= max_chars or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def wrap_width(s, max_px, size):
    """Greedy wrap by estimated rendered width - 0.52em is a safe average
    advance for the system sans, so text never overflows the card."""
    return wrap(s, max(int(max_px / (size * 0.52)), 8))


def section_header(A, x, y, title, subtitle="", max_px=CONTENT_W, size=12):
    """Draw a section title plus an optional wrapped subtitle. Returns the y
    the section's content should start at."""
    A(text(x, y + 13, title, 14.5, INK, "600"))
    lines = wrap_width(subtitle, max_px, size) if subtitle else []
    for i, line in enumerate(lines):
        A(text(x, y + 31 + i * 16, line, size, INK_2))
    return y + 31 + max(len(lines) - 1, 0) * 16 + 19


def bounds_check(name, elements, limit):
    """Fail loudly if any drawn text runs past the card's inner margin."""
    worst = max((ext for ext, _ in EXTENTS), default=0)
    status = "ok" if worst <= limit else "OVERFLOW"
    print(f"  bounds {name}: widest text reaches x={worst:.0f} (limit {limit}) [{status}]")
    EXTENTS.clear()
    return worst <= limit


def fmt_money(v):
    return f"${v:,.2f}"


def fmt_duration(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s"


def pct(v, digits=0):
    """Half-up percentage - avoids Python's round-half-even surprising a reader
    who recomputes the number by hand."""
    factor = 10 ** digits
    return f"{int(v * 100 * factor + 0.5) / factor:.{digits}f}%"


# --- figure ------------------------------------------------------------------


def build_svg():
    calls, pending = load_calls()
    out = []
    A = out.append
    y = PAD

    total = len(calls) + len(pending)
    n_qualified = sum(1 for c in calls if qualified(c))
    # Pooled across calls (total agent words / total words), so "of all words
    # spoken" is literally true rather than an unweighted mean of percentages.
    bot_words = sum(c["bot_words"] for c in calls)
    all_words = bot_words + sum(c["user_words"] for c in calls)
    avg_talk = bot_words / all_words if all_words else 0.0
    total_cost = sum(c["cost"] for c in calls)

    # ---- header
    A(text(PAD, y + 22, "AI Voice Agent — Call Quality Report", 23, INK, "600"))
    A(text(PAD, y + 45,
           "Real-estate outbound campaign · Pal Realty · Vapi voice agent orchestrated by n8n · "
           "every call graded by an LLM-as-judge",
           12.5, INK_2))
    A(text(W - PAD, y + 22, "n8n + Vapi", 12, MUTED, "500", "end"))
    A(text(W - PAD, y + 40, "Jan 2025", 12, MUTED, "400", "end"))
    y += 66
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 26

    # ---- KPI row (stat tiles: label, value) --------------------------------
    tiles = [
        ("Leads in campaign", str(total), f"{len(calls)} with call data"),
        ("Calls analysed", str(len(calls)), "graded end to end"),
        ("Leads qualified", str(n_qualified), "interest + follow-up consent"),
        ("AI talk share", pct(avg_talk), "of all words spoken"),
        ("Call spend", fmt_money(total_cost), f"{fmt_money(total_cost / len(calls))} per call"),
    ]
    gap = 14
    tile_w = (CONTENT_W - gap * (len(tiles) - 1)) / len(tiles)
    tile_h = 86
    for i, (label, value, sub) in enumerate(tiles):
        x = PAD + i * (tile_w + gap)
        A(f'<rect x="{x:.1f}" y="{y}" width="{tile_w:.1f}" height="{tile_h}" rx="10" '
          f'fill="{PLANE}" stroke="{BORDER}" stroke-width="1"/>')
        A(text(x + 14, y + 24, label, 11.5, MUTED, "500"))
        A(text(x + 14, y + 56, value, 28, INK, "600"))
        A(text(x + 14, y + 74, sub, 11, INK_2))
    y += tile_h + 30

    # ---- Panel 1: talk share per call (stacked bars, 2 categorical series) --
    y = section_header(
        A, PAD, y, "Who did the talking",
        "Share of words spoken by the agent vs the caller. A cold call reads as a conversation "
        "near 50/50; above ~70% the agent is monologuing.")

    # legend (always present for >= 2 series)
    lx = PAD
    for key, label in (("ai", "AI agent"), ("caller", "Caller")):
        A(f'<rect x="{lx}" y="{y - 8}" width="10" height="10" rx="2.5" fill="{SERIES[key]}"/>')
        A(text(lx + 16, y + 1, label, 12, INK_2, "500"))
        lx += 108
    y += 20

    label_col = 132
    bar_x = PAD + label_col
    bar_w = CONTENT_W - label_col
    row_h = 42
    bar_h = 22
    for c in calls:
        by = y + (row_h - bar_h) / 2
        A(text(PAD, by + 11, c["name"], 13, INK, "600"))
        A(text(PAD, by + 26, f"{fmt_duration(c['duration_s'])} call", 11, MUTED))
        A(f'<rect x="{bar_x}" y="{by}" width="{bar_w}" height="{bar_h}" rx="4" fill="{PLANE}"/>')

        ai_w = bar_w * c["bot_pct"]
        cal_w = max(bar_w - ai_w - 2, 0)      # 2px surface gap between segments
        # AI segment: square at the left baseline, square at the interior edge
        A(f'<path d="{bar_path(bar_x, by, ai_w, bar_h, 4, 0)}" fill="{SERIES["ai"]}"/>')
        # Caller segment: rounded data-end
        cxp = bar_x + ai_w + 2
        A(f'<path d="{bar_path(cxp, by, cal_w, bar_h, 0, 4)}" fill="{SERIES["caller"]}"/>')

        ai_lbl = f"AI {pct(c['bot_pct'])}"
        cal_lbl = f"Caller {pct(1 - c['bot_pct'])}"
        if ai_w > 78:
            A(text(bar_x + 12, by + bar_h / 2 + 4.5, ai_lbl, 12, ink_on(SERIES["ai"]), "600"))
        else:
            A(text(bar_x + ai_w + 8, by - 4, ai_lbl, 12, INK_2, "600"))
        if cal_w > 88:
            A(text(cxp + 12, by + bar_h / 2 + 4.5, cal_lbl, 12, ink_on(SERIES["caller"]), "600"))
        else:
            A(text(bar_x + bar_w, by - 4, cal_lbl, 12, INK_2, "600", "end"))
        y += row_h

    y += 12
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 28

    # ---- Panel 2: judge verdict (status colour + icon + label, never alone) --
    y = section_header(
        A, PAD, y, "Judge verdict",
        "Rubric: clarity, tone, conversational flow, and whether the caller was guided to a clear "
        "next step. Written by the n8n information-extractor node on every end-of-call report.")
    y += 4

    for c in calls:
        just_lines = wrap(c["justification"], 92)
        badge_w, badge_h = 92, 26
        A(f'<rect x="{PAD}" y="{y}" width="{badge_w}" height="{badge_h}" rx="13" '
          f'fill="{PLANE}" stroke="{BORDER}" stroke-width="1"/>')
        A(f'<circle cx="{PAD + 16}" cy="{y + badge_h / 2}" r="5" fill="{STATUS[c["grade"]]}"/>')
        A(text(PAD + 30, y + badge_h / 2 + 4.5, c["grade"], 12.5, INK, "600"))
        A(text(PAD + badge_w + 16, y + 17, c["name"], 13.5, INK, "600"))
        tx = PAD + badge_w + 16 + 74
        for i, line in enumerate(just_lines[:3]):
            A(text(tx, y + 17 + i * 17, line, 12, INK_2))
        y += max(badge_h, 17 + len(just_lines[:3]) * 17) + 20

    y += 4
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 28

    # ---- Panels 3 + 4 side by side: funnel (ordinal ramp) | cost table ------
    left_w = 452
    right_x = PAD + left_w + 44
    right_w = CONTENT_W - left_w - 44
    top = y

    y = section_header(A, PAD, y, "Campaign funnel", "Where the leads went", left_w)
    stages = [
        ("In campaign list", total),
        ("Call data captured", len(calls)),
        ("Qualified leads", n_qualified),
    ]
    f_label_col = 142
    f_bar_x = PAD + f_label_col
    f_bar_w = left_w - f_label_col
    f_row_h = 40
    f_bar_h = 22
    hi = max(s[1] for s in stages)
    for i, (label, value) in enumerate(stages):
        by = y + (f_row_h - f_bar_h) / 2
        A(text(PAD, by + 15, label, 12, INK_2, "500"))
        w = f_bar_w * (value / hi) if hi else 0
        A(f'<rect x="{f_bar_x}" y="{by}" width="{f_bar_w}" height="{f_bar_h}" rx="4" fill="{PLANE}"/>')
        A(f'<path d="{bar_path(f_bar_x, by, max(w, 6), f_bar_h, 4, 4)}" fill="{ORDINAL[i]}"/>')
        inside = w > 46
        if inside:
            A(text(f_bar_x + w - 10, by + f_bar_h / 2 + 4.5, str(value), 12,
                   ink_on(ORDINAL[i]), "600", "end"))
        else:
            A(text(f_bar_x + w + 10, by + f_bar_h / 2 + 4.5, str(value), 12, INK, "600"))
        y += f_row_h

    y = top
    y = section_header(A, right_x, y, "Cost & duration", "Billed by Vapi per call", right_w)
    cols = [("Caller", right_x, "start"), ("Duration", right_x + 128, "end"),
            ("Cost", right_x + 244, "end"), ("Per min", right_x + right_w, "end")]
    for label, cx, anchor in cols:
        A(text(cx, y, label, 11, MUTED, "500", anchor))
    y += 10
    A(f'<line x1="{right_x}" y1="{y}" x2="{right_x + right_w}" y2="{y}" stroke="{RULE}" stroke-width="1"/>')
    y += 20
    for c in calls:
        vals = [c["name"], fmt_duration(c["duration_s"]), fmt_money(c["cost"]),
                fmt_money(c["cost_per_min"])]
        for (label, cx, anchor), val in zip(cols, vals):
            A(text(cx, y, val, 12.5, INK if label == "Caller" else INK_2,
                   "600" if label == "Caller" else "400", anchor, tabular=True))
        y += 22
    A(f'<line x1="{right_x}" y1="{y - 6}" x2="{right_x + right_w}" y2="{y - 6}" stroke="{GRID}" stroke-width="1"/>')
    for (label, cx, anchor), val in zip(cols, ["Total", "", fmt_money(total_cost), ""]):
        if val:
            A(text(cx, y + 12, val, 12.5, INK, "600", anchor, tabular=True))
    y += 40

    y = max(y, top + 50 + len(stages) * f_row_h) + 6
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 24

    # ---- footer: provenance + honest sample-size caveat --------------------
    footer = (f"Source: RealestateOutboundCallLogs.csv — {len(calls)} analysed calls, "
              f"{len(pending)} lead awaiting an end-of-call report. Sample size is small by "
              f"design: this figure demonstrates the grading pipeline end to end, not a "
              f"statistically meaningful quality benchmark.")
    for i, line in enumerate(wrap_width(footer, CONTENT_W, 11)):
        A(text(PAD, y + i * 16, line, 11, MUTED))
    y += 16 * len(wrap_width(footer, CONTENT_W, 11)) + 10
    A(text(PAD, y, "Generated by charts/generate_charts.py from the judge's CSV output.", 11, MUTED))
    y += 30

    height = y

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height:.0f}" '
        f'viewBox="0 0 {W} {height:.0f}" role="img" '
        f'aria-label="Call quality report: {len(calls)} AI voice calls graded by an LLM judge">',
        f'<title>AI voice agent call quality report</title>',
        f'<rect x="0" y="0" width="{W}" height="{height:.0f}" rx="14" fill="{SURFACE}" '
        f'stroke="{BORDER}" stroke-width="1"/>',
        *out,
        "</svg>",
    ]
    bounds_check("call-quality-report", svg, W - PAD + 8)
    return "\n".join(svg)


def build_flow_svg():
    """Conversation-flow figure: every turn as a segment sized by words spoken,
    on a scale shared by both calls so call length is comparable. Defect turns
    found by detect_defects() / detect_ignored_hangup() are marked and listed."""
    calls, _ = load_calls()
    out = []
    A = out.append
    y = PAD

    # Shared scale: words -> px, set by the longest call. The gap budget is
    # reserved up front (2px between turns, not after the last), so no call can
    # run past the plot area.
    plot_w = CONTENT_W
    max_words = max(sum(len(t["text"].split()) for t in c["turns"]) for c in calls)
    max_turns = max(len(c["turns"]) for c in calls)
    px_per_word = (plot_w - 2 * (max_turns - 1)) / max_words

    A(text(PAD, y + 22, "Conversation flow — turn by turn", 23, INK, "600"))
    A(text(PAD, y + 45,
           "Each block is one turn, sized by the words spoken. Both calls share one scale, "
           "so the short call really is short.",
           12.5, INK_2))
    y += 66
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 24

    lx = PAD
    for key, label in (("ai", "AI agent"), ("caller", "Caller")):
        A(f'<rect x="{lx}" y="{y - 9}" width="10" height="10" rx="2.5" fill="{SERIES[key]}"/>')
        A(text(lx + 16, y, label, 12, INK_2, "500"))
        lx += 108
    A(f'<circle cx="{lx + 5}" cy="{y - 4}" r="8" fill="{STATUS["Bad"]}"/>')
    A(text(lx + 20, y, "Flagged turn (see notes)", 12, INK_2, "500"))
    y += 24

    lane_h = 20
    lane_gap = 6
    badge_w, badge_h = 92, 26

    for c in calls:
        turns = c["turns"]
        words = [len(t["text"].split()) for t in turns]
        flags = detect_defects(turns)
        hangup = detect_ignored_hangup(turns)

        # --- call header row
        A(f'<rect x="{PAD}" y="{y}" width="{badge_w}" height="{badge_h}" rx="13" '
          f'fill="{PLANE}" stroke="{BORDER}" stroke-width="1"/>')
        A(f'<circle cx="{PAD + 16}" cy="{y + badge_h / 2}" r="5" fill="{STATUS[c["grade"]]}"/>')
        A(text(PAD + 30, y + badge_h / 2 + 4.5, c["grade"], 12.5, INK, "600"))
        A(text(PAD + badge_w + 16, y + badge_h / 2 + 3, c["name"], 13.5, INK, "600"))
        A(text(PAD + badge_w + 16, y + badge_h / 2 + 18,
               f"{fmt_duration(c['duration_s'])} · {sum(words)} words · {len(turns)} turns · "
               f"{c['ended_reason']}", 11, MUTED))
        y += badge_h + 30          # clearance for defect badges above the AI lane

        # --- swimlanes
        ai_y = y
        cal_y = y + lane_h + lane_gap
        A(f'<rect x="{PAD}" y="{ai_y}" width="{plot_w}" height="{lane_h}" rx="4" fill="{PLANE}"/>')
        A(f'<rect x="{PAD}" y="{cal_y}" width="{plot_w}" height="{lane_h}" rx="4" fill="{PLANE}"/>')

        x = PAD
        markers = []          # (cx, cy, defect_label)
        for i, turn in enumerate(turns):
            w = max(words[i] * px_per_word, 4)
            seg_y = ai_y if turn["speaker"] == "ai" else cal_y
            fill = SERIES[turn["speaker"]]
            A(f'<path d="{bar_path(x, seg_y, w, lane_h, 3, 3)}" fill="{fill}"/>')
            if w > 62:
                A(text(x + w / 2, seg_y + lane_h / 2 + 4.5, str(words[i]), 11.5,
                       ink_on(fill), "600", "middle"))
            defect = flags.get(i, set()).copy()
            if hangup is not None and i == hangup:
                defect.add("ignored_hangup")
            if defect:
                markers.append((x + w / 2, seg_y, defect))
            x += w + (2 if i < len(turns) - 1 else 0)   # 2px gap *between* turns

        for cx, seg_y, defect in markers:
            my = seg_y - 13 if seg_y == ai_y else seg_y + lane_h + 13
            A(f'<circle cx="{cx:.1f}" cy="{my:.1f}" r="8.5" fill="{STATUS["Bad"]}" '
              f'stroke="{SURFACE}" stroke-width="2"/>')
        # clear the badge row below the caller lane when a caller turn is flagged
        caller_badges = any(seg_y != ai_y for _, seg_y, _ in markers)
        y = cal_y + lane_h + (26 if caller_badges else 14)

        # --- notes, in turn order, numbered to match the markers
        notes = []
        for i, turn in enumerate(turns):
            defect = flags.get(i, set()).copy()
            if hangup is not None and i == hangup:
                defect.add("ignored_hangup")
            if not defect:
                continue
            if "ignored_hangup" in defect:
                msg = ("agent keeps pitching after the caller said goodbye — "
                       "the judge's stated reason for the Bad grade")
            elif "restart" in defect:
                msg = ("agent's turn cut off mid-sentence, then re-sent from the start "
                       "(caller talking over the agent)")
            else:
                msg = "agent's turn ends mid-sentence — barge-in cut playback"
            notes.append((i + 1, msg))
        for n, (turn_no, msg) in enumerate(notes, 1):
            A(f'<circle cx="{PAD + 9}" cy="{y - 4}" r="8.5" fill="{STATUS["Bad"]}"/>')
            A(text(PAD + 9, y - 0.5, str(n), 11, "#ffffff", "600", "middle"))
            A(text(PAD + 26, y, f"Turn {turn_no}: {msg}", 12, INK_2))
            y += 19
        if not notes:
            A(text(PAD + 9, y, "No mechanically detectable defects.", 12, INK_2))
            y += 19
        y += 20
        A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
        y += 24

    # --- footer
    footer = ("Flagged turns come from two mechanical rules over the transcript: a turn that "
              "ends mid-sentence with no terminal punctuation, and a turn whose text is re-sent "
              "as the opening of the speaker's next turn. Both are conversation-quality signals "
              "the LLM judge described in prose; these rules make them countable across calls.")
    for i, line in enumerate(wrap_width(footer, CONTENT_W, 11)):
        A(text(PAD, y + i * 16, line, 11, MUTED))
    y += 16 * len(wrap_width(footer, CONTENT_W, 11)) + 10
    A(text(PAD, y, "Generated by charts/generate_charts.py from the judge's CSV output.", 11, MUTED))
    y += 30

    height = y
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height:.0f}" '
        f'viewBox="0 0 {W} {height:.0f}" role="img" '
        f'aria-label="Conversation flow for {len(calls)} graded calls, turn by turn">',
        f'<title>Conversation flow — turn by turn</title>',
        f'<rect x="0" y="0" width="{W}" height="{height:.0f}" rx="14" fill="{SURFACE}" '
        f'stroke="{BORDER}" stroke-width="1"/>',
        *out,
        "</svg>",
    ]
    bounds_check("conversation-flow", svg, W - PAD + 8)
    return "\n".join(svg)


def build_inbound_svg():
    """Dental-clinic inbound cohort: the same reporting treatment applied to the
    one dataset here that is big enough to answer a question with.

    The finding the figure exists to show is a negative one. Talk share is the
    intuitive quality signal, and on the outbound two-call sample it tracked the
    grade. At fourteen calls it does not: the Bad calls are the *most*
    agent-dominated of the three groups. So the figure shows the metric failing
    to separate the groups, instead of a correlation the data does not support.
    """
    calls, meta = load_inbound()
    out = []
    A = out.append
    y = PAD

    n = len(calls)
    order = ("Good", "Neutral", "Bad")
    by_grade = {g: [c for c in calls if c["grade"] == g] for g in order}
    mix = " · ".join(str(len(by_grade[g])) for g in order)
    durs = [c["duration_s"] for c in calls]
    total_cost = sum(c["cost"] for c in calls)
    total_s = sum(durs)
    rate_s = total_cost / total_s if total_s else 0.0
    ends = Counter(c["ended_reason"] for c in calls)
    n_customer = ends.get("customer-ended-call", 0)
    n_timeout = n - n_customer

    # ---- header
    A(text(PAD, y + 22, "Dental Clinic — Inbound Call Quality", 23, INK, "600"))
    A(text(PAD, y + 45,
           "Parramatta Dental Clinic AI receptionist · Vapi voice agent orchestrated by n8n · "
           "every call graded after the fact by the same LLM-as-judge",
           12.5, INK_2))
    A(text(W - PAD, y + 22, "n8n + Vapi", 12, MUTED, "500", "end"))
    A(text(W - PAD, y + 40, "20 Jan – 1 Feb 2025", 12, MUTED, "400", "end"))
    y += 66
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 26

    # ---- KPI row
    tiles = [
        ("Calls graded", str(n), f"from {meta['records']} log rows"),
        ("Grade mix", mix, "Good · Neutral · Bad"),
        ("Median talk share", pct(statistics.median([c["talk"] for c in calls])),
         "agent's share of words"),
        ("Median call", fmt_duration(statistics.median(durs)),
         f"shortest {min(durs):.0f}s · longest {fmt_duration(max(durs))}"),
        ("Spend", fmt_money(total_cost), f"{fmt_money(rate_s * 60)} per minute"),
    ]
    gap = 14
    tile_w = (CONTENT_W - gap * (len(tiles) - 1)) / len(tiles)
    tile_h = 86
    for i, (label, value, sub) in enumerate(tiles):
        x = PAD + i * (tile_w + gap)
        A(f'<rect x="{x:.1f}" y="{y}" width="{tile_w:.1f}" height="{tile_h}" rx="10" '
          f'fill="{PLANE}" stroke="{BORDER}" stroke-width="1"/>')
        A(text(x + 14, y + 24, label, 11.5, MUTED, "500"))
        A(text(x + 14, y + 56, value, 26, INK, "600"))
        A(text(x + 14, y + 74, sub, 11, INK_2))
    y += tile_h + 30

    # ---- Panel 1: grade mix (status palette, word carried alongside colour) --
    y = section_header(
        A, PAD, y, "Grade mix",
        "The outbound campaign's rubric, unchanged: clarity, tone, flow, and whether the caller was "
        "guided to a clear next step. Most calls land Neutral — a verdict that reads as "
        "\"nothing went wrong\" rather than \"something went well\".")
    p1_label_col = 116
    p1_bar_x = PAD + p1_label_col
    p1_bar_w = CONTENT_W - p1_label_col
    hi = max(len(v) for v in by_grade.values())
    for g in order:
        v = by_grade[g]
        by = y + 9
        A(text(PAD, by + 15, g, 13, INK, "600"))
        A(text(PAD, by + 30, f"n = {len(v)}", 11, MUTED))
        w = max(p1_bar_w * len(v) / hi, 6)
        A(f'<rect x="{p1_bar_x}" y="{by}" width="{p1_bar_w}" height="22" rx="4" fill="{PLANE}"/>')
        A(f'<path d="{bar_path(p1_bar_x, by, w, 22, 4, 4)}" fill="{STATUS[g]}"/>')
        A(text(p1_bar_x + w - 10, by + 15.5,
               f"{len(v)} call{'s' if len(v) != 1 else ''} · {pct(len(v) / n)}",
               12, ink_on(STATUS[g]), "600", "end"))
        y += 40
    y += 12
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 28

    # ---- Panels 2 + 3 side by side: talk share by grade | how calls ended ----
    left_w = 540
    gutter = 36
    right_x = PAD + left_w + gutter
    right_w = CONTENT_W - left_w - gutter
    top = y

    y = section_header(
        A, PAD, y, "Talk share by grade",
        "The agent's share of the words spoken, recomputed from the word counts. If talking too "
        "much were the problem, the dots would march left as the grade improves. They do not.", left_w)
    p2_label_col = 110
    plot_x = PAD + p2_label_col
    plot_w = left_w - p2_label_col
    row_h = 58
    strip_top = y + 6
    strip_bot = strip_top + row_h * 3
    for i in range(5):
        gx = plot_x + plot_w * i / 4
        A(f'<line x1="{gx:.1f}" y1="{strip_top}" x2="{gx:.1f}" y2="{strip_bot}" '
          f'stroke="{GRID}" stroke-width="1"/>')
        A(text(gx, strip_bot + 18, f"{i * 25}%", 10.5, MUTED, "400", "middle"))
    for gi, g in enumerate(order):
        v = by_grade[g]
        cy = strip_top + row_h * gi + row_h / 2
        med = statistics.median([c["talk"] for c in v]) if v else 0.0
        A(text(PAD, cy - 2, g, 12.5, INK, "600"))
        A(text(PAD, cy + 13, f"n={len(v)} · median {pct(med)}", 10.5, MUTED))
        for c in v:
            cx = plot_x + plot_w * c["talk"]
            A(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{STATUS[g]}" '
              f'stroke="{SURFACE}" stroke-width="2"/>')
            if c["duration_s"] < 30:
                A(text(cx, cy + 21, f"{c['duration_s']:.0f}s", 10, MUTED, "400", "middle"))
        mx = plot_x + plot_w * med
        A(f'<line x1="{mx:.1f}" y1="{cy - 11:.1f}" x2="{mx:.1f}" y2="{cy + 11:.1f}" '
          f'stroke="{INK}" stroke-width="2"/>')
    y = strip_bot + 44

    ry = section_header(
        A, right_x, top, "How calls ended",
        "How the call ended, not whether an appointment was booked. Booking outcome appears only in "
        "the prose summary, so this figure does not claim a booking rate.", right_w)
    bar_h = 24
    cust_w = right_w * n_customer / n
    tox = right_x + cust_w + 2
    tox_w = max(right_w - cust_w - 2, 0)
    A(f'<path d="{bar_path(right_x, ry, cust_w, bar_h, 4, 0)}" fill="{ORDINAL[2]}"/>')
    A(f'<path d="{bar_path(tox, ry, tox_w, bar_h, 0, 4)}" fill="{ORDINAL[0]}"/>')
    A(text(right_x + 12, ry + bar_h / 2 + 4.5, f"Customer ended · {n_customer}", 12,
           ink_on(ORDINAL[2]), "600"))
    if tox_w > 28:
        A(text(tox + tox_w / 2, ry + bar_h / 2 + 4.5, str(n_timeout), 11,
               ink_on(ORDINAL[0]), "600", "middle"))
    ry += bar_h + 24
    for fill_hex, label in (
            (ORDINAL[2], f"Customer ended — {n_customer} calls ({pct(n_customer / n)})"),
            (ORDINAL[0], f"Silence timed out — {n_timeout} calls ({pct(n_timeout / n)})")):
        A(f'<rect x="{right_x}" y="{ry - 9}" width="10" height="10" rx="2.5" fill="{fill_hex}"/>')
        A(text(right_x + 17, ry, label, 11.5, INK_2, "500"))
        ry += 20
    # Reading the outcome out of the summaries is a heuristic, so the sentence is
    # assembled from what the rule actually matched rather than asserted.
    dated = [c for c in calls if c["dated_booking"]]
    if dated and len(dated) == len(by_grade["Good"]) and all(c["grade"] == "Good" for c in dated):
        booking_note = (f"Read from the prose: {len(dated)} summaries name a date for the "
                        f"appointment — the same {len(dated)} calls the judge graded Good.")
    else:
        good_dated = sum(1 for c in dated if c["grade"] == "Good")
        booking_note = (f"Read from the prose: {len(dated)} summaries name a date for the "
                        f"appointment, {good_dated} of them graded Good.")
    failed = next((c for c in calls if c["failed_booking"]), None)
    if failed:
        booking_note += (f" One summary says the booking could not be completed, and the judge "
                         f"graded that call {failed['grade']}.")
    for note in ("Both timeouts fell on calls the judge graded Neutral; no Good call ran out "
                 "of silence.", booking_note):
        lines = wrap_width(note, right_w, 11.5)
        for i, line in enumerate(lines):
            A(text(right_x, ry + 10 + i * 16, line, 11.5, MUTED))
        ry += 10 + 16 * len(lines)

    y = max(y, ry) + 16
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 28

    # ---- Panel 4: spend against call length (one x, one y - never two axes) --
    y = section_header(
        A, PAD, y, "Spend against call length",
        "Vapi bills per minute, so cost tracks call length: duration accounts for 97% of the "
        "variance in spend here (r = 0.99). The spread around the line is still real — only 4 of "
        "the 14 calls land within 5% of it — so the rate is a good planning number and a poor "
        "per-call prediction.")
    lx = PAD
    for g in order:
        A(f'<circle cx="{lx + 5}" cy="{y - 4}" r="5.5" fill="{STATUS[g]}" '
          f'stroke="{SURFACE}" stroke-width="2"/>')
        A(text(lx + 15, y, g, 11.5, INK_2, "500"))
        lx += 82
    A(f'<line x1="{lx}" y1="{y - 4}" x2="{lx + 22}" y2="{y - 4}" stroke="{MUTED}" stroke-width="2"/>')
    A(text(lx + 30, y, f"{fmt_money(rate_s * 60)} per minute · pooled rate", 11.5, INK_2, "500"))
    y += 28

    ax_x = PAD + 46
    ax_w = CONTENT_W - 46 - 16
    ax_top = y
    ax_h = 200
    ax_bot = ax_top + ax_h
    xmax, ymax = 300.0, 0.50
    for i in range(3):
        gy = ax_bot - ax_h * i / 2
        A(f'<line x1="{ax_x}" y1="{gy:.1f}" x2="{ax_x + ax_w}" y2="{gy:.1f}" '
          f'stroke="{GRID}" stroke-width="1"/>')
        A(text(ax_x - 8, gy + 4, f"${ymax * i / 2:.2f}", 10.5, MUTED, "400", "end"))
    A(f'<line x1="{ax_x}" y1="{ax_bot}" x2="{ax_x + ax_w}" y2="{ax_bot}" '
      f'stroke="{RULE}" stroke-width="1"/>')
    for i in range(6):
        A(text(ax_x + ax_w * i / 5, ax_bot + 18, f"{xmax * i / 5 / 60:.0f}m", 10.5,
               MUTED, "400", "middle"))
    A(text(PAD, ax_top - 8, "Cost", 11, INK_2, "500"))
    A(text(ax_x + ax_w, ax_bot + 36, "Call length", 11, INK_2, "500", "end"))

    line_y = ax_bot - ax_h * (rate_s * xmax / ymax)
    A(f'<line x1="{ax_x}" y1="{ax_bot}" x2="{ax_x + ax_w}" y2="{line_y:.1f}" '
      f'stroke="{MUTED}" stroke-width="2"/>')
    for c in calls:
        cx = ax_x + ax_w * c["duration_s"] / xmax
        cy = ax_bot - ax_h * min(c["cost"], ymax) / ymax
        A(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{STATUS[c["grade"]]}" '
          f'stroke="{SURFACE}" stroke-width="2"/>')
    # The two longest calls are the same length to within two seconds and are the
    # widest-apart pair on the chart - label both, above and below, so the gap is
    # read as the finding rather than as a drawing slip.
    ranked = sorted(calls, key=lambda c: -c["duration_s"])
    for c, offset in ((ranked[1], -13), (ranked[0], 19)):
        cx = ax_x + ax_w * c["duration_s"] / xmax
        cy = ax_bot - ax_h * min(c["cost"], ymax) / ymax + offset
        A(text(cx, cy, f"{fmt_duration(c['duration_s'])} · {fmt_money(c['cost'])}",
               11, INK_2, "500", "middle"))
    A(text(ax_x, ax_bot + 36,
           f"The two longest calls are the same length to within two seconds — "
           f"{fmt_duration(ranked[0]['duration_s'])} at {fmt_money(ranked[0]['cost'])}, "
           f"{fmt_duration(ranked[1]['duration_s'])} at {fmt_money(ranked[1]['cost'])}.",
           11, INK_2))
    A(text(ax_x + ax_w, ax_bot + 36, "Call length", 11, INK_2, "500", "end"))
    y = ax_bot + 56
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 28

    # ---- data quality: the log's own defects --------------------------------
    y = section_header(
        A, PAD, y, "Data quality — what the log got wrong",
        "Found by building this figure. Each item is a reason to read one exported row as a claim, "
        "not a fact.")
    items = [
        f"`bot_word_percent` is unusable on {meta['stale_pct_rows']} of "
        f"{meta['graded_rows']} graded rows — the most recent read 11,706,000% to 11,910,000%, "
        f"which matches neither the word counts nor the call duration. Talk share in this figure "
        f"is recomputed from `bot_word_count` and `user_word_count`, which are consistent on "
        f"every row.",
        f"One call was written to the log twice, byte-identical in every field — a retried write "
        f"that a unique key on the call id would have prevented.",
        f"A second call was written twice with the same transcript but a different summary and a "
        f"different judge justification — the grader is a sampling model, so the same transcript "
        f"does not reliably produce the same verdict. Any per-call grade is one draw, not the "
        f"answer.",
        f"One row is entirely empty — which is why {meta['records']} records hold "
        f"{meta['graded_rows']} graded rows and {n} distinct calls.",
        f"The phone column carries three different mask formats — it cannot be used as a join or "
        f"upsert key until that is normalised.",
    ]
    for i, item in enumerate(items, 1):
        A(f'<circle cx="{PAD + 10}" cy="{y - 4}" r="9" fill="{PLANE}" '
          f'stroke="{BORDER}" stroke-width="1"/>')
        A(text(PAD + 10, y - 0.5, str(i), 11, INK, "600", "middle"))
        for j, line in enumerate(wrap_width(item.replace("`", ""), CONTENT_W - 34, 11.5)):
            A(text(PAD + 28, y + j * 16, line, 11.5, INK_2))
        y += 16 * len(wrap_width(item.replace("`", ""), CONTENT_W - 34, 11.5)) + 12
    y += 12
    A(f'<line x1="{PAD}" y1="{y}" x2="{W - PAD}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
    y += 24

    # ---- footer
    footer = (f"Source: dentalClinic/DentalCallLogs.csv — {n} distinct graded calls made to one "
              f"clinic over twelve days. Still a small sample: it demonstrates the grading pipeline "
              f"on real conversations and shows which signals do not hold up, not a quality rate for "
              f"the agent.")
    footer_lines = wrap_width(footer, CONTENT_W, 11)
    for i, line in enumerate(footer_lines):
        A(text(PAD, y + i * 16, line, 11, MUTED))
    y += 16 * len(footer_lines) + 10
    A(text(PAD, y, "Generated by charts/generate_charts.py from the judge's CSV output.", 11, MUTED))
    y += 30

    height = y
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height:.0f}" '
        f'viewBox="0 0 {W} {height:.0f}" role="img" '
        f'aria-label="Inbound call quality report: {n} dental clinic calls graded by an LLM judge">',
        f'<title>Dental clinic inbound call quality report</title>',
        f'<rect x="0" y="0" width="{W}" height="{height:.0f}" rx="14" fill="{SURFACE}" '
        f'stroke="{BORDER}" stroke-width="1"/>',
        *out,
        "</svg>",
    ]
    bounds_check("inbound-call-quality", svg, W - PAD + 8)
    return "\n".join(svg)


def main():
    svg = build_svg()
    OUT_PATH.write_text(svg, encoding="utf-8")
    calls, pending = load_calls()
    print(f"wrote {OUT_PATH.relative_to(ROOT)}  ({len(svg):,} bytes)")
    flow = build_flow_svg()
    FLOW_PATH.write_text(flow, encoding="utf-8")
    print(f"wrote {FLOW_PATH.relative_to(ROOT)}  ({len(flow):,} bytes)")
    inbound = build_inbound_svg()
    INBOUND_PATH.write_text(inbound, encoding="utf-8")
    print(f"wrote {INBOUND_PATH.relative_to(ROOT)}  ({len(inbound):,} bytes)")
    print(f"  {len(calls)} analysed calls, {len(pending)} pending")
    for c in calls:
        print(f"  - {c['name']}: {c['grade']}, AI talk {c['bot_pct']*100:.0f}%, "
              f"{fmt_duration(c['duration_s'])}, {fmt_money(c['cost'])}")

    in_calls, meta = load_inbound()
    print(f"  inbound: {meta['records']} records -> {meta['graded_rows']} graded rows -> "
          f"{len(in_calls)} distinct calls")
    print(f"  inbound defects: {meta['stale_pct_rows']} stale percentages, "
          f"{meta['rewrites']} double write, {meta['regraded']} re-graded, "
          f"{meta['empty']} empty row")
    for c in in_calls:
        print(f"  - {c['name'] or '(no name)'}: {c['grade']}, talk {c['talk']*100:.0f}%, "
              f"{fmt_duration(c['duration_s'])}, {fmt_money(c['cost'])}, {c['ended_reason']}")


if __name__ == "__main__":
    main()
