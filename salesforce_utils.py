"""Shared Salesforce session, report, and User-lookup utilities."""

import os
import re
import time

from playwright.async_api import async_playwright
from simple_salesforce import Salesforce

REPORT_ID_RE = re.compile(r"(00O[A-Za-z0-9]{12,15})")
OPP_ID_RE = re.compile(r"(006[A-Za-z0-9]{12,15})")
CDP_URL = "http://localhost:9222"
SF_HOST_HINT = "salesforce.com"
SF_LOGIN_URL = os.getenv("SALESFORCE_LOGIN_URL") or "https://login.salesforce.com/"
SHEET_ID_COLUMN = "Opportunity ID"


class SetupError(RuntimeError):
    """User-actionable setup problem — printed without a traceback."""


async def read_sf_session_from_chrome() -> tuple[str, str]:
    """Return (session_id, instance_host) from the Chrome debug session."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            try:
                if not browser.contexts:
                    raise SetupError(
                        "Chrome is running on :9222 but has no open contexts. "
                        "Open a tab and navigate to your Salesforce instance."
                    )
                sids = [
                    cookie
                    for ctx in browser.contexts
                    for cookie in await ctx.cookies()
                    if cookie["name"] == "sid" and SF_HOST_HINT in cookie["domain"]
                ]
            finally:
                await browser.close()
    except SetupError:
        raise
    except Exception as error:
        raise SetupError(
            f"Could not connect to Chrome at {CDP_URL}. Launch the dedicated Chrome "
            f"profile and complete login on {SF_LOGIN_URL}.\nUnderlying error: {error}"
        )

    if not sids:
        raise SetupError(
            "No Salesforce `sid` cookie found in Chrome. "
            "In Chrome (already running on :9222), open "
            f"{SF_LOGIN_URL} and complete login, then try again."
        )
    # Prefer the org's My Domain session over login.salesforce.com's.
    sid = next((c for c in sids if c["domain"].endswith(".my.salesforce.com")), sids[0])
    return sid["value"], sid["domain"].lstrip(".")


def find_user_id(sf: Salesforce, name: str) -> str:
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    query = sf.query(
        f"SELECT Id, Name, Username FROM User WHERE Name = '{escaped}' AND IsActive = true"
    )
    records = query["records"]
    if not records:
        raise SetupError(f"No active Salesforce user named '{name}'.")
    if len(records) > 1:
        detail = "\n  ".join(f"{record['Id']} ({record['Username']})" for record in records)
        raise SetupError(f"Multiple active users named '{name}':\n  {detail}")
    return records[0]["Id"]


def run_report_async(sf: Salesforce, report_id: str, timeout_s: int = 120) -> dict:
    """Run a Salesforce report asynchronously and return its detail rows."""
    instance = sf.restful(
        f"analytics/reports/{report_id}/instances?includeDetails=true",
        method="POST",
        json={},
    )
    instance_id = instance["id"]

    deadline = time.monotonic() + timeout_s
    delay = 1.0
    while time.monotonic() < deadline:
        result = sf.restful(
            f"analytics/reports/{report_id}/instances/{instance_id}", method="GET"
        )
        status = result.get("attributes", {}).get("status") or result.get("status")
        if status == "Success":
            return result
        if status == "Error":
            raise SetupError(
                f"Async report {report_id} ended with status Error. "
                f"Response: {result.get('attributes', {}).get('errorMessage', result)}"
            )
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)
    raise SetupError(f"Async report {report_id} did not finish within {timeout_s}s.")
