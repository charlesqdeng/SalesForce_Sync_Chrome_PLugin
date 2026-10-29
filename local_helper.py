"""Loopback API used by the Chrome extension to export and sync report data."""

import asyncio
import contextlib
import io
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
from urllib.parse import urlsplit

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceExpiredSession

from salesforce_utils import (
    OPP_ID_RE,
    REPORT_ID_RE,
    CDP_URL,
    SF_LOGIN_URL,
    SetupError,
    read_sf_session_from_chrome,
    run_report_async,
)
from sf_to_sheet import (
    ID_HEADER,
    URL_HEADER,
    _cell,
    lightning_base_url,
    read_sheet_with_baseline,
    write_sheet,
)
from sheet_sync import (
    apply,
    find_edits,
    load_rejected,
    plan,
    refresh_baseline,
    row_opp_key,
    skip_rejected,
    update_rejected,
)
from sheets_browser import SHEET_ID_RE

HOST = "127.0.0.1"
PORT = 8765
MAX_REQUEST_BYTES = 1_000_000
EXTENSION_ID = "nmikjpppphnlclgjboaiblbpickjlmde"
ID_FIELD_ALIASES = {
    "ACCOUNT_NAME": "AccountId",
    "OWNER_NAME": "OwnerId",
}


def _validate_urls(report_url: object, sheet_url: object) -> tuple[str, str]:
    if not isinstance(report_url, str) or not isinstance(sheet_url, str):
        raise SetupError("Report and sheet URLs must be text.")
    report_url = report_url.strip()
    sheet_url = sheet_url.strip()
    if not REPORT_ID_RE.search(report_url):
        raise SetupError("Enter a valid Salesforce report URL containing a report ID.")
    parsed_sheet_url = urlsplit(sheet_url)
    if (parsed_sheet_url.scheme != "https"
            or parsed_sheet_url.netloc != "docs.google.com"
            or not SHEET_ID_RE.search(parsed_sheet_url.path)):
        raise SetupError("Enter a valid Google Sheets URL.")
    return report_url, sheet_url


async def _salesforce() -> tuple[Salesforce, str]:
    session_id, instance = await read_sf_session_from_chrome()
    return Salesforce(instance=instance, session_id=session_id), instance


def _report_id(report_url: str) -> str:
    match = REPORT_ID_RE.search(report_url)
    if not match:
        raise SetupError("Could not read the Salesforce report ID from the configured URL.")
    return match.group(1)


def _report_schema(sf: Salesforce, report: dict) -> list[dict]:
    """One entry per report detail column, with its sheet header and Opportunity field."""
    metadata = report.get("reportMetadata") or {}
    columns = metadata.get("detailColumns") or []
    if not columns:
        raise SetupError("This Salesforce report has no detail columns to export.")

    extended = (report.get("reportExtendedMetadata") or {}).get("detailColumnInfo") or {}
    describe = sf.Opportunity.describe()["fields"]
    by_name = {field["name"].lower(): field for field in describe}
    by_label = {field["label"].lower(): field for field in describe}
    schema = []
    headers = []
    for column in columns:
        if "." in column:
            prefix, final_name = column.rsplit(".", 1)
        else:
            final_name = column
            prefix = "OPPORTUNITY"
        field = by_name.get(final_name.lower()) if prefix.upper() == "OPPORTUNITY" else None
        if field is None and prefix.upper() == "OPPORTUNITY":
            alias = ID_FIELD_ALIASES.get(final_name.upper())
            field = by_name.get(alias.lower()) if alias else None
        info = extended.get(column) or {}
        label = (info.get("label") or (field or {}).get("label")
                 or final_name.replace("_", " ").title())
        if field is None and prefix.upper() == "OPPORTUNITY":
            field = by_label.get(label.lower())
        is_id = prefix.upper() == "OPPORTUNITY" and final_name.upper() in ("ID", "OPPORTUNITY_ID")
        header = label.strip()
        # Keep the app's own ID and URL headers unambiguous.
        if is_id and ID_HEADER not in headers:
            header = ID_HEADER
        elif header in (ID_HEADER, URL_HEADER):
            header = f"{header} (Report)"
        if header in headers:
            suffix = 2
            while f"{header} ({suffix})" in headers:
                suffix += 1
            header = f"{header} ({suffix})"
        headers.append(header)
        schema.append({
            "report_column": column,
            "header": header,
            "field": field,
            "is_id": is_id,
        })
    return schema


def _record_id(row: dict, schema: list[dict]) -> Optional[str]:
    cells = row.get("dataCells") or []
    id_indexes = [i for i, column in enumerate(schema) if column["is_id"]]
    candidates = [cells[i] for i in id_indexes if i < len(cells)]
    candidates.extend(cells)
    for cell in candidates:
        value = cell.get("value")
        if isinstance(value, dict):
            value = value.get("id") or value.get("Id")
        if isinstance(value, str):
            match = OPP_ID_RE.search(value)
            if match:
                return match.group(1)
    return None


def _editable_columns(schema: list[dict]) -> dict[str, dict]:
    """{sheet header: Opportunity field describe} for columns that can sync back."""
    columns = {}
    for column in schema:
        field = column["field"]
        is_user_lookup = (
            field
            and field.get("type") == "reference"
            and "User" in (field.get("referenceTo") or [])
        )
        if (field and field.get("updateable") and not column["is_id"]
                and (field.get("type") != "reference" or is_user_lookup)):
            columns[column["header"]] = field
    return columns


def _report_rows(report: dict, schema: list[dict], instance: str,
                 report_url: str) -> tuple[list[str], list[list[str]]]:
    source_headers = [column["header"] for column in schema]
    id_index = next((i for i, column in enumerate(schema) if column["is_id"]), None)
    insert_id = id_index is None
    if insert_id:
        id_index = 1 if source_headers else 0
        source_headers.insert(id_index, ID_HEADER)
    source_headers.append(URL_HEADER)
    base_url = lightning_base_url(instance, report_url)

    rows = []
    seen = set()
    fact_map = report.get("factMap") or {}
    for key, group in fact_map.items():
        if not key.endswith("!T"):
            continue
        for row in group.get("rows") or []:
            opp_id = _record_id(row, schema)
            if not opp_id or opp_id[:15] in seen:
                continue
            seen.add(opp_id[:15])
            cells = row.get("dataCells") or []
            values = []
            for i in range(len(schema)):
                cell = cells[i] if i < len(cells) else {}
                value = cell.get("label")
                if value is None or cell.get("value") in (None, ""):
                    # Reports label empty fields "-"; export them as blank cells.
                    value = cell.get("value")
                if isinstance(value, dict):
                    value = value.get("name") or value.get("Name") or ""
                values.append(_cell(value))
            if insert_id:
                values.insert(id_index, opp_id)
            else:
                values[id_index] = opp_id
            values.append(f"{base_url}/lightning/r/Opportunity/{opp_id}/view")
            rows.append(values)
    if not rows:
        raise SetupError(
            "No Opportunity detail rows with IDs were found. Include the Opportunity ID "
            "column in the Salesforce report, then try again."
        )
    return source_headers, rows


def _check_export_safe(sheet_url: str) -> None:
    rows, baseline, tab = read_sheet_with_baseline(sheet_url)
    if baseline is None or not rows:
        return
    data_headers = [header for header in rows[0] if header not in (ID_HEADER, URL_HEADER)]
    edits, _ = find_edits(rows, baseline, data_headers, allow_clear=True)
    edits = skip_rejected(edits, load_rejected(sheet_url))
    rows_without_ids = [
        row for row in rows
        if not row_opp_key(row) and any(row.get(header) for header in data_headers)
    ]
    if edits or rows_without_ids:
        raise SetupError(
            f"Tab '{tab}' has unsynced or unkeyed sheet changes. Sync editable changes "
            "or restore the edited cells before exporting; export would overwrite them."
        )


def export_report(report_url: str, sheet_url: str, discard_edits: bool = False) -> dict:
    """Replace the sheet with the report. discard_edits skips the unsynced-edit check."""
    report_url, sheet_url = _validate_urls(report_url, sheet_url)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        sf, instance = asyncio.run(_salesforce())
        report = run_report_async(sf, _report_id(report_url))
        schema = _report_schema(sf, report)
        headers, rows = _report_rows(report, schema, instance, report_url)
        editable = _editable_columns(schema)
        if not discard_edits:
            _check_export_safe(sheet_url)
        tab = write_sheet(sheet_url, rows, header=headers)
    return {
        "message": f"Exported {len(rows)} report row(s) to tab “{tab}”.",
        "rowCount": len(rows),
        "columnCount": len(headers),
        "editableColumns": list(editable),
        "log": output.getvalue(),
    }


def sync_sheet(report_url: str, sheet_url: str, dry_run: bool) -> dict:
    report_url, sheet_url = _validate_urls(report_url, sheet_url)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        sf, _ = asyncio.run(_salesforce())
        report = run_report_async(sf, _report_id(report_url))
        fields = _editable_columns(_report_schema(sf, report))
        headers = list(fields)
        rows, baseline, tab = read_sheet_with_baseline(sheet_url)
        if baseline is None:
            raise SetupError("This worksheet has not been exported yet. Export the report first.")
        if not rows:
            raise SetupError(f"Sheet tab '{tab}' has no data rows. Export the report first.")
        missing = [name for name in [ID_HEADER, *headers] if name not in rows[0]]
        if missing:
            raise SetupError(f"Sheet tab '{tab}' has no column(s): {', '.join(missing)}.")
        edits, notes = find_edits(rows, baseline, headers, allow_clear=False)
        for note in notes:
            print(f"  {note}", file=sys.stderr)
        read_only_headers = [
            header for header in rows[0]
            if header not in (ID_HEADER, URL_HEADER, *headers)
        ]
        read_only_edits, _ = find_edits(
            rows, baseline, read_only_headers, allow_clear=True
        )
        if read_only_edits:
            ignored = sorted({edit["header"] for edit in read_only_edits})
            print(
                f"  {len(read_only_edits)} edit(s) ignored in read-only report columns: "
                + ", ".join(ignored)
            )
        rejected = load_rejected(sheet_url)
        edits = skip_rejected(edits, rejected)
        if not edits:
            return {
                "message": (
                    "No editable cells to sync."
                    if not read_only_edits else
                    "No editable cells to sync. Changes in read-only report columns were ignored."
                ),
                "counts": {"push": 0, "applied": 0, "conflict": 0, "error": 0},
                "log": output.getvalue(),
            }

        plan(sf, edits, fields, force=False)
        apply(sf, edits, fields, dry_run)
        counts = {action: sum(edit["action"] == action for edit in edits)
                  for action in ("push", "applied", "conflict", "error")}
        if not dry_run:
            refresh_baseline(sheet_url, rows, baseline, edits)
            update_rejected(sheet_url, rejected, edits)
    return {
        "message": (
            f"{'Preview' if dry_run else 'Sync'}: {counts['push']} update(s), "
            f"{counts['applied']} already current, {counts['conflict']} conflict(s), "
            f"{counts['error']} error(s)."
        ),
        "counts": counts,
        "log": output.getvalue(),
    }


class ExtensionHandler(BaseHTTPRequestHandler):
    server_version = "SalesforceSheetHelper/1.0"

    def _allowed_origin(self) -> Optional[str]:
        origin = self.headers.get("Origin", "")
        if origin == f"chrome-extension://{EXTENSION_ID}":
            return origin
        return None

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The popup closed before the operation finished; its result is in this log.
            self.log_message("Popup closed before the response was sent: %s",
                             data.get("message") or data.get("error"))

    def do_OPTIONS(self) -> None:
        origin = self._allowed_origin()
        if not origin:
            self.send_error(403, "Only Chrome extensions may access this helper.")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"error": "Not found."})
            return
        self._send_json(200, {"ok": True})

    def do_POST(self) -> None:
        if not self._allowed_origin():
            self._send_json(403, {"error": "Only Chrome extensions may access this helper."})
            return
        if self.headers.get("Host") not in (f"{HOST}:{PORT}", f"localhost:{PORT}"):
            self._send_json(403, {"error": "Unexpected local helper host."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise SetupError("Invalid request size.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise SetupError("Request body must be a JSON object.")
            if self.path == "/api/export":
                result = export_report(
                    payload.get("reportUrl", ""),
                    payload.get("sheetUrl", ""),
                    payload.get("discardEdits") is True,
                )
            elif self.path == "/api/sync":
                result = sync_sheet(
                    payload.get("reportUrl", ""),
                    payload.get("sheetUrl", ""),
                    bool(payload.get("dryRun", True)),
                )
            else:
                self._send_json(404, {"error": "Not found."})
                return
            self._send_json(200, result)
        except (SetupError, SalesforceExpiredSession) as error:
            self._send_json(400, {"error": str(error)})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Request body must be valid JSON."})
        except Exception as error:
            self.log_error("Local helper request failed: %s", error)
            self._send_json(500, {"error": str(error) or "The operation failed."})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[helper] {fmt % args}", file=sys.stderr)


def main() -> int:
    try:
        server = HTTPServer((HOST, PORT), ExtensionHandler)
    except OSError as error:
        print(f"Cannot start the local helper at http://{HOST}:{PORT}: {error}", file=sys.stderr)
        return 1
    print(f"Chrome extension helper listening at http://{HOST}:{PORT}")
    print(f"Salesforce session must be available in Chrome at {CDP_URL} ({SF_LOGIN_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local helper.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
