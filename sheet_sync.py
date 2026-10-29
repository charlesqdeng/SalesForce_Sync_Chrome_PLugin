"""Changed-cell planning and Salesforce sync operations used by the extension."""

import re
import sys
from datetime import datetime
from typing import Optional

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError

from salesforce_utils import (
    OPP_ID_RE,
    SHEET_ID_COLUMN,
    SetupError,
    find_user_id,
)
from sf_to_sheet import (
    URL_HEADER,
    _cell,
    _get_path,
    query_opportunities,
    read_rejected,
    soql_path,
    write_baseline,
    write_rejected,
)

NUMERIC_TYPES = ("double", "currency", "percent", "int", "long")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
RECORD_ID_RE = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?")


def row_opp_key(row: dict[str, str]) -> Optional[str]:
    """15-char Opportunity Id from the ID column (or the URL column as fallback)."""
    for col in (SHEET_ID_COLUMN, URL_HEADER):
        match = OPP_ID_RE.search(row.get(col, ""))
        if match:
            return match.group(1)[:15]
    return None


def _norm_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def normalize(value: str, field: dict) -> str:
    """Canonical form for comparing a sheet cell with a Salesforce value."""
    text = _norm_text(value)
    if not text:
        return ""
    ftype = field["type"]
    if ftype == "boolean":
        low = text.lower()
        if low in ("true", "1", "yes", "y"):
            return "true"
        if low in ("false", "0", "no", "n"):
            return "false"
    elif ftype in NUMERIC_TYPES:
        try:
            return repr(float(text.replace(",", "").rstrip("%").lstrip("$")))
        except ValueError:
            pass
    elif ftype == "date":
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
    elif ftype == "multipicklist":
        return ";".join(sorted(p.strip() for p in text.split(";") if p.strip()))
    return text


class SyncPlanError(ValueError):
    """A sheet value that can't be written to its field."""


def to_payload(sf: Salesforce, field: dict, text: str, user_ids: dict[str, str]) -> object:
    """Turn a sheet cell into the value Salesforce's REST API expects."""
    text = _norm_text(text)
    if not text:
        return None
    ftype = field["type"]
    if ftype == "reference":
        if RECORD_ID_RE.fullmatch(text) and any(c.isdigit() for c in text):
            return text  # a raw record Id
        if "User" not in (field.get("referenceTo") or []):
            raise SyncPlanError(
                f"lookup to {'/'.join(field.get('referenceTo') or ['?'])} — "
                f"put a record Id in the cell, names only work for User lookups"
            )
        if text not in user_ids:
            try:
                user_ids[text] = find_user_id(sf, text)
            except SetupError as e:
                raise SyncPlanError(str(e).splitlines()[0])
        return user_ids[text]
    norm = normalize(text, field)
    if ftype == "boolean":
        if norm not in ("true", "false"):
            raise SyncPlanError(f"checkbox needs true/false, got {text!r}")
        return norm == "true"
    if ftype in NUMERIC_TYPES:
        try:
            number = float(norm)
        except ValueError:
            raise SyncPlanError(f"not a number: {text!r}")
        if ftype in ("int", "long"):
            if not number.is_integer():
                raise SyncPlanError(f"whole number required, got {text!r}")
            return int(number)
        return number
    if ftype == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", norm):
            raise SyncPlanError(f"date must be YYYY-MM-DD or M/D/YYYY, got {text!r}")
        return norm
    if ftype == "picklist":
        allowed = {v["value"] for v in field.get("picklistValues") or [] if v.get("active")}
        if allowed and text not in allowed:
            raise SyncPlanError(f"{text!r} is not an active picklist value")
    return text


def find_edits(rows: list[dict[str, str]], baseline: Optional[tuple[list[str], list[dict]]],
               headers: list[str], allow_clear: bool) -> tuple[list[dict], list[str]]:
    """Cells that differ from the baseline in the given columns.

    Returns (edits, notes). Each edit is {row, key, header, sheet, base} where
    base is None if the opp has no baseline row.
    """
    base_rows = {}
    for b in baseline[1] if baseline else []:
        key = row_opp_key(b)
        if key:
            base_rows.setdefault(key, b)

    edits, notes, seen = [], [], set()
    for i, row in enumerate(rows, 2):
        key = row_opp_key(row)
        if not key:
            if any(row.get(h) for h in headers):
                notes.append(f"row {i}: no Opportunity ID — ignored")
            continue
        if key in seen:
            notes.append(f"row {i}: duplicate of an earlier row for {key} — ignored")
            continue
        seen.add(key)
        base = base_rows.get(key)
        for h in headers:
            sheet_val = row.get(h, "")
            base_val = base.get(h, "") if base is not None else None
            if base_val is not None and _norm_text(sheet_val) == _norm_text(base_val):
                continue
            if not _norm_text(sheet_val):
                if base_val is None:
                    continue
                if not allow_clear:
                    notes.append(f"row {i} {key} '{h}': cleared in sheet — ignored")
                    continue
            edits.append({"row": i, "key": key, "header": h,
                          "sheet": sheet_val, "base": base_val})
    return edits, notes


def load_rejected(sheet_url: str) -> dict[tuple[str, str], dict[str, str]]:
    """{(15-char opp Id, column): log row} for cells Salesforce rejected."""
    rejected = {}
    for r in read_rejected(sheet_url):
        key = row_opp_key(r)
        if key and r.get("Column"):
            rejected[(key, r["Column"])] = r
    return rejected


def skip_rejected(edits: list[dict], rejected: dict[tuple[str, str], dict]) -> list[dict]:
    """Drop edits whose value is the one Salesforce already rejected."""
    kept = []
    for e in edits:
        r = rejected.get((e["key"], e["header"]))
        if r and _norm_text(r.get("Rejected value", "")) == _norm_text(e["sheet"]):
            print(f"  row {e['row']} {e['key']} '{e['header']}'  skip — rejected "
                  f"{r.get('When', '')}: {r.get('Error', '')} (change the cell to retry)",
                  file=sys.stderr)
            continue
        kept.append(e)
    return kept


def update_rejected(sheet_url: str, rejected: dict[tuple[str, str], dict],
                    edits: list[dict]) -> None:
    """Keep Salesforce validation failures in the local per-sheet state."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new = dict(rejected)
    for e in edits:
        cell = (e["key"], e["header"])
        if e["action"] in ("push", "applied"):
            new.pop(cell, None)
        elif e["action"] == "error" and e.get("rejected"):
            new[cell] = {SHEET_ID_COLUMN: e.get("id", e["key"]), "Column": e["header"],
                         "Rejected value": e["sheet"], "Error": e["reason"], "When": now}
    if new != rejected:
        write_rejected(sheet_url, list(new.values()))


def salesforce_message(err: SalesforceError) -> str:
    """Human-readable message(s) from a Salesforce API error."""
    content = getattr(err, "content", None)
    if isinstance(content, list):
        msgs = [c.get("message", "") for c in content if isinstance(c, dict)]
        if any(msgs):
            return "; ".join(m for m in msgs if m)
    return str(err).splitlines()[0]


def plan(sf: Salesforce, edits: list[dict], fields: dict[str, dict],
         force: bool) -> list[dict]:
    """Compare each edit with Salesforce and decide its action, in place."""
    paths = sorted({soql_path(f) for f in fields.values()})
    records = query_opportunities(sf, sorted({e["key"] for e in edits}), paths)
    user_ids: dict[str, str] = {}
    for e in edits:
        field = fields[e["header"]]
        record = records.get(e["key"])
        if record is None:
            e["action"], e["reason"] = "error", "opportunity not found or not readable"
            continue
        e["id"] = record["Id"]
        current = _cell(_get_path(record, soql_path(field)))
        e["current"] = current
        ns, nf = normalize(e["sheet"], field), normalize(current, field)
        if ns == nf:
            e["action"] = "applied"
            continue
        if e["base"] is not None:
            # Older exports wrote empty fields as the report's "-" placeholder.
            base = "" if e["base"].strip() == "-" and nf == "" else e["base"]
            conflict = nf != normalize(base, field)
            reason = "changed in Salesforce since the export"
        else:
            conflict = nf != ""
            reason = "row not in last export and Salesforce already has a value"
        if conflict and not force:
            e["action"], e["reason"] = "conflict", reason
            continue
        try:
            e["payload"] = to_payload(sf, field, e["sheet"], user_ids)
            e["action"] = "push"
        except SyncPlanError as err:
            e["action"], e["reason"] = "error", str(err)
    return edits


def _short(text: Optional[str], n: int = 60) -> str:
    text = (text or "").replace("\n", " ⏎ ")
    return repr(text if len(text) <= n else text[:n - 1] + "…")


def apply(sf: Salesforce, edits: list[dict], fields: dict[str, dict], dry_run: bool) -> None:
    """Print the plan; unless dry_run, write each opp's pushes in one update."""
    by_opp: dict[str, list[dict]] = {}
    for e in edits:
        by_opp.setdefault(e["key"], []).append(e)
    for key, opp_edits in by_opp.items():
        pushes = [e for e in opp_edits if e["action"] == "push"]
        if pushes and not dry_run:
            _push(sf, pushes, fields)
        for e in opp_edits:
            where = f"row {e['row']} {key} '{e['header']}'"
            action = e["action"]
            if action == "push":
                verb = "WOULD UPDATE" if dry_run else "updated"
                print(f"  {where}  {verb}: {_short(e['current'])} → {_short(e['sheet'])}")
            elif action == "applied":
                print(f"  {where}  skip — Salesforce already has {_short(e['sheet'])}")
            elif action == "conflict":
                print(f"  {where}  CONFLICT — {e['reason']}\n"
                      f"      exported: {_short(e['base'])}  Salesforce: "
                      f"{_short(e['current'])}  sheet: {_short(e['sheet'])}",
                      file=sys.stderr)
            else:
                print(f"  {where}  ERROR: {e['reason']}", file=sys.stderr)


def _push(sf: Salesforce, pushes: list[dict], fields: dict[str, dict]) -> None:
    """Write an opp's pushes in one update. If Salesforce rejects it, retry
    each field alone so one bad field doesn't block the rest.
    """
    def update(batch):
        sf.Opportunity.update(batch[0]["id"],
                              {fields[e["header"]]["name"]: e["payload"] for e in batch})

    try:
        update(pushes)
        return
    except SalesforceError as err:
        if len(pushes) == 1:
            pushes[0].update(action="error", reason=salesforce_message(err), rejected=True)
            return
    for e in pushes:
        try:
            update([e])
        except SalesforceError as err:
            e.update(action="error", reason=salesforce_message(err), rejected=True)


def refresh_baseline(sheet_url: str, rows: list[dict[str, str]],
                     baseline: Optional[tuple[list[str], list[dict]]],
                     edits: list[dict]) -> int:
    """Record applied cells in the baseline. Returns the number of cells recorded.

    Opps with no baseline row get one only if every edit on them was applied.
    Otherwise their conflicts would turn into pushes on the next run.
    """
    header, base_rows = (list(baseline[0]), baseline[1]) if baseline else ([], [])
    base_by_key = {}
    for b in base_rows:
        key = row_opp_key(b)
        if key:
            base_by_key.setdefault(key, b)
    sheet_by_key = {}
    for r in rows:
        key = row_opp_key(r)
        if key:
            sheet_by_key.setdefault(key, r)

    done = [e for e in edits if e["action"] in ("push", "applied")]
    failed_keys = {e["key"] for e in edits if e["action"] not in ("push", "applied")}
    recorded = 0
    for e in done:
        base = base_by_key.get(e["key"])
        if base is None:
            if e["key"] in failed_keys:
                continue
            base = dict(sheet_by_key[e["key"]])
            base_by_key[e["key"]] = base
            base_rows.append(base)
            header.extend(h for h in base if h not in header)
        base[e["header"]] = e["sheet"]
        if e["header"] not in header:
            header.append(e["header"])
        recorded += 1
    if recorded:
        write_baseline(sheet_url, header, base_rows)
    return recorded
