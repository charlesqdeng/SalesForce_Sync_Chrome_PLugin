"""Salesforce Opportunity and Google Sheets operations used by the extension."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from simple_salesforce import Salesforce
from sheets_browser import SHEET_ID_RE, replace_sheet, sheet_gid
from sheets_browser import read_sheet as read_sheet_in_browser

from salesforce_utils import (
    SHEET_ID_COLUMN,
    SetupError,
)

URL_HEADER = "Opportunity URL"
ID_HEADER = SHEET_ID_COLUMN

# Google Sheets rejects cells longer than 50,000 characters.
MAX_CELL_CHARS = 50_000


def soql_path(field: dict) -> str:
    """SOQL path for a field. Lookup fields (e.g. a User) resolve to
    "<Relationship>.Name" so the sheet shows the record name instead of a raw Id.
    """
    if field["type"] == "reference" and field.get("relationshipName"):
        return f"{field['relationshipName']}.Name"
    return field["name"]


def lightning_base_url(instance: str, report_url: str) -> str:
    """Base URL for record links — the report URL's host if it's a Lightning
    URL, else derived from the API instance (x.my.salesforce.com → x.lightning.force.com).
    """
    match = re.match(r"https://[^/]+\.lightning\.force\.com", report_url)
    if match:
        return match.group(0)
    return "https://" + instance.replace(".my.salesforce.com", ".lightning.force.com")


def query_opportunities(sf: Salesforce, opp_ids: list[str],
                        paths: list[str]) -> dict[str, dict]:
    """Records keyed by 15-char Id (so 15- and 18-char Ids both match)."""
    records: dict[str, dict] = {}
    select = ", ".join(dict.fromkeys(["Id", *paths]))
    for i in range(0, len(opp_ids), 200):
        chunk = opp_ids[i:i + 200]
        id_list = ", ".join(f"'{x}'" for x in chunk)
        result = sf.query_all(f"SELECT {select} FROM Opportunity WHERE Id IN ({id_list})")
        for r in result["records"]:
            records[r["Id"][:15]] = r
    return records


def _get_path(record: dict, path: str) -> object:
    """Read a dotted SOQL path like "Owner.Name"; None if any hop is empty."""
    value: object = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS - 20] + "\n…[truncated]"
    return text


def _state_path(sheet_url: str) -> Path:
    """Local state file for one worksheet (spreadsheet ID + tab gid)."""
    match = SHEET_ID_RE.search(urlsplit(sheet_url).path)
    if not match:
        raise SetupError("Enter a valid Google Sheets URL in the extension.")
    key = f"{match.group(1)}:{sheet_gid(sheet_url)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return Path.home() / ".salesforce-sheet-sync" / f"{digest}.json"


def _read_state(sheet_url: str) -> dict:
    path = _state_path(sheet_url)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as state_file:
        return json.load(state_file)


def _write_state(sheet_url: str, state: dict) -> None:
    path = _state_path(sheet_url)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def read_sheet_with_baseline(sheet_url: str) -> tuple[
        list[dict[str, str]], Optional[tuple[list[str], list[dict[str, str]]]], str]:
    """Read the active worksheet and the last-export baseline saved locally.

    Returns ([], None, title) when this worksheet has never been exported.
    """
    raw_baseline = _read_state(sheet_url).get("baseline")
    if not raw_baseline:
        return [], None, "active worksheet"
    header = raw_baseline["header"]
    baseline_rows = raw_baseline["rows"]
    _, rows, title = read_sheet_in_browser(sheet_url)
    return rows, (header, baseline_rows), title


def write_sheet(sheet_url: str, rows: list[list[str]], header: list[str]) -> str:
    """Replace the active worksheet via clipboard and save its local baseline."""
    title = replace_sheet(sheet_url, header, rows, header.index(ID_HEADER))
    baseline_rows = [
        {name: (row[index] if index < len(row) else "") for index, name in enumerate(header)}
        for row in rows
    ]
    state = _read_state(sheet_url)
    state["baseline"] = {"header": header, "rows": baseline_rows}
    state["rejected"] = []
    _write_state(sheet_url, state)
    return title


def read_rejected(sheet_url: str) -> list[dict[str, str]]:
    return _read_state(sheet_url).get("rejected", [])


def write_rejected(sheet_url: str, rows: list[dict[str, str]]) -> None:
    state = _read_state(sheet_url)
    state["rejected"] = rows
    _write_state(sheet_url, state)


def write_baseline(sheet_url: str, header: list[str], rows: list[dict[str, str]]) -> None:
    state = _read_state(sheet_url)
    state["baseline"] = {"header": header, "rows": rows}
    _write_state(sheet_url, state)
