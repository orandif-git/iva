"""Builds the Excel files. Pure functions: rows in, bytes out, so the contents can be tested exactly.

Every text cell is written as a string, never as a formula. A camera name or a note a customer typed could start with "=" and would
otherwise run as a formula when someone opens the file (formula injection)."""
import io
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill

HEAD_FONT = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="0F766E")
MAX_ROWS = 200_000          # Excel allows a million; a file this size is already slow to open on an office PC


def _text(ws, v) -> WriteOnlyCell:
    c = WriteOnlyCell(ws, value="" if v is None else str(v))
    c.data_type = "s"
    return c


def _cell(ws, v):
    if isinstance(v, str):
        return _text(ws, v)
    return v


def _head(ws, names):
    cells = []
    for n in names:
        c = _text(ws, n)
        c.font, c.fill, c.alignment = HEAD_FONT, HEAD_FILL, Alignment(vertical="center")
        cells.append(c)
    ws.append(cells)


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = "A2"
    _head(ws, headers)
    return ws


def _finish(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz).replace(tzinfo=None)             # Excel has no time zones: show the tenant's own clock


def gate_register(rows: list, tz_name: str, truncated: bool = False) -> bytes:
    """rows: seen_at, plate_text, plate_raw, vehicle_type, gate, direction, confidence, review_status, dwell_seconds, list_type"""
    tz = ZoneInfo(tz_name)
    wb = Workbook(write_only=True)
    ws = _sheet(wb, "Gate register", ["Date", "Time", "Plate", "Vehicle", "Gate", "Direction", "Confidence %", "Checked", "Time inside (h:mm)", "List"], [12, 10, 16, 14, 22, 11, 13, 14, 17, 10])
    daily = defaultdict(lambda: {"in": 0, "out": 0, "plates": set(), "blocked": 0, "review": 0})
    for r in rows:
        t = _local(r["seen_at"], tz)
        d = daily[t.date()]
        if r["direction"] in ("in", "out"):
            d[r["direction"]] += 1
        if r["plate_text"]:
            d["plates"].add(r["plate_text"])
        d["blocked"] += r["list_type"] == "blocked"
        d["review"] += r["review_status"] == "needs_review"
        dwell = r["dwell_seconds"]
        ws.append([t.date(), t.time().replace(microsecond=0), _text(ws, r["plate_text"] or f"? {r['plate_raw'] or ''}"), _text(ws, r["vehicle_type"]), _text(ws, r["gate"]),
                   _text(ws, {"in": "In", "out": "Out"}.get(r["direction"], "Unknown")), r["confidence"],
                   _text(ws, {"needs_review": "Needs checking", "confirmed": "Confirmed", "corrected": "Corrected"}.get(r["review_status"], "Automatic")),
                   _text(ws, f"{dwell // 3600}:{dwell % 3600 // 60:02d}" if dwell is not None else ""), _text(ws, {"blocked": "Blocked", "allowed": "Allowed"}.get(r["list_type"], ""))])
    s = _sheet(wb, "Daily summary", ["Date", "Entries", "Exits", "Different vehicles", "Blocked vehicle sightings", "Plates needing a check"], [12, 10, 10, 18, 26, 22])
    for day in sorted(daily):
        d = daily[day]
        s.append([day, d["in"], d["out"], len(d["plates"]), d["blocked"], d["review"]])
    if truncated:
        n = wb.create_sheet("Read me")
        n.append([_text(n, f"This file holds the first {MAX_ROWS:,} reads of the period. There were more. Choose a shorter period to see them all.")])
    return _finish(wb)


def footfall_summary(rows: list, tz_name: str, days: int) -> bytes:
    """rows: day (date), hour (0-23), camera, people_in, people_out  (already in the tenant's local time)"""
    wb = Workbook(write_only=True)
    ws = _sheet(wb, "By day and camera", ["Date", "Camera", "People in", "People out", "Net"], [12, 24, 12, 12, 10])
    per_cam, per_day, per_hour = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in rows:
        per_cam[(r["day"], r["camera"])][0] += r["people_in"]
        per_cam[(r["day"], r["camera"])][1] += r["people_out"]
        per_day[r["day"]][0] += r["people_in"]
        per_day[r["day"]][1] += r["people_out"]
        per_hour[r["hour"]][0] += r["people_in"]
        per_hour[r["hour"]][1] += r["people_out"]
    for (day, cam), (i, o) in sorted(per_cam.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        ws.append([day, _text(ws, cam), i, o, i - o])
    t = _sheet(wb, "By day", ["Date", "People in", "People out", "Net"], [12, 12, 12, 10])
    for day in sorted(per_day):
        t.append([day, per_day[day][0], per_day[day][1], per_day[day][0] - per_day[day][1]])
    h = _sheet(wb, "By hour of day", ["Hour", "People in (total)", "People out (total)", "People in (average per day)"], [10, 18, 18, 26])
    for hr in range(24):
        i, o = per_hour[hr]
        h.append([_text(h, f"{hr:02d}:00"), i, o, round(i / max(days, 1), 1)])
    return _finish(wb)


def vehicle_audit(vehicles: list, inside: list, tz_name: str, now: datetime) -> bytes:
    """vehicles: plate_text, first_seen, last_seen, entries, exits, dwell_total, dwell_n, list_type   inside: plate_text, seen_at, gate, vehicle_type, list_type"""
    tz = ZoneInfo(tz_name)
    wb = Workbook(write_only=True)
    ws = _sheet(wb, "Vehicles", ["Plate", "First seen", "Last seen", "Entries", "Exits", "Total time inside (h)", "Average visit (h)", "List"], [16, 18, 18, 9, 9, 20, 17, 10])
    for v in vehicles:
        avg = v["dwell_total"] / v["dwell_n"] / 3600 if v["dwell_n"] else None
        ws.append([_text(ws, v["plate_text"]), _local(v["first_seen"], tz), _local(v["last_seen"], tz), v["entries"], v["exits"], round((v["dwell_total"] or 0) / 3600, 2),
                   round(avg, 2) if avg is not None else None, _text(ws, {"blocked": "Blocked", "allowed": "Allowed"}.get(v["list_type"], ""))])
    s = _sheet(wb, "Still inside", ["Plate", "Entered", "Gate", "Vehicle", "Hours inside so far", "List"], [16, 18, 22, 14, 20, 10])
    for r in inside:
        s.append([_text(s, r["plate_text"]), _local(r["seen_at"], tz), _text(s, r["gate"]), _text(s, r["vehicle_type"]), round((now - r["seen_at"]).total_seconds() / 3600, 1),
                  _text(s, {"blocked": "Blocked", "allowed": "Allowed"}.get(r["list_type"], ""))])
    return _finish(wb)
