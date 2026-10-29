"""Read and write Google Sheets through the signed-in Chrome user interface."""

import asyncio
import csv
import io
import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, async_playwright

from salesforce_utils import CDP_URL, SetupError

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
LOAD_TIMEOUT_MS = 60_000
PASTE_CHECK_ATTEMPTS = 30
SIGNED_OUT_MESSAGE = (
    "Google is not signed in in the dedicated Chrome window. Sign in to "
    "Google there, then retry the sheet operation."
)


def sheet_gid(url: str) -> str:
    parsed = urlsplit(url)
    return (
        parse_qs(parsed.query).get("gid")
        or parse_qs(parsed.fragment).get("gid")
        or [""]
    )[0]


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _clipboard_tsv(header: list[str], rows: list[list[str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    for row in [header, *rows]:
        safe_row = []
        for value in row:
            text = str(value)
            # Store as literal text so values round-trip exactly and never run as formulas.
            if text.lstrip().startswith(("=", "+", "-", "@")):
                text = "'" + text
            safe_row.append(text)
        writer.writerow(safe_row)
    return output.getvalue()


async def _name_box(page: Page):
    locator = page.locator(
        '#t-name-box, input.waffle-name-box, input[aria-label*="name box" i]'
    ).first
    await locator.wait_for(state="visible", timeout=LOAD_TIMEOUT_MS)
    return locator


async def _open_sheet(browser, sheet_url: str) -> Page:
    match = SHEET_ID_RE.search(urlsplit(sheet_url).path)
    if not match:
        raise SetupError("Enter a valid Google Sheets URL in the extension.")
    sheet_id = match.group(1)
    requested_gid = sheet_gid(sheet_url)
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    await context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin="https://docs.google.com"
    )
    page = next((candidate for candidate in context.pages
                 if f"/spreadsheets/d/{sheet_id}" in candidate.url), None)
    if page is None:
        page = await context.new_page()
        await page.goto(sheet_url, wait_until="domcontentloaded")
    elif requested_gid and sheet_gid(page.url) != requested_gid:
        # A gid-only change is a same-document navigation, so reload to switch tabs.
        await page.goto(sheet_url, wait_until="domcontentloaded")
        await page.reload(wait_until="domcontentloaded")
    # The async Clipboard API needs a focused document. Emulate focus instead of
    # activating the tab, which would close the extension popup.
    cdp = await context.new_cdp_session(page)
    await cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})

    if "accounts.google.com" in page.url:
        raise SetupError(SIGNED_OUT_MESSAGE)
    try:
        await _name_box(page)
    except Exception as error:
        if "accounts.google.com" in page.url:
            raise SetupError(SIGNED_OUT_MESSAGE) from error
        raise SetupError(
            "The Google Sheet did not open in the dedicated Chrome profile. "
            "Check sheet access and try again."
        ) from error
    # Sheets silently opens another tab when the requested gid no longer exists.
    if requested_gid and sheet_gid(page.url) != requested_gid:
        raise SetupError(
            f"The sheet tab in the saved Google Sheet URL (gid={requested_gid}) no longer "
            f"exists; Google opened “{await _tab_title(page)}” instead. Open the tab you "
            "want, copy its URL from the address bar, and save it in the extension."
        )
    return page


async def _tab_title(page: Page) -> str:
    tab = page.locator(".docs-sheet-tab.docs-sheet-active-tab").first
    try:
        return (await tab.inner_text(timeout=2_000)).strip() or "active worksheet"
    except Exception:
        return "active worksheet"


async def _select_range(page: Page, cell_range: str) -> None:
    name_box = await _name_box(page)
    await name_box.fill(cell_range)
    await name_box.press("Enter")


async def _select_all(page: Page) -> None:
    """Select every cell. Ranges like A:ZZ fail on sheets with fewer columns."""
    await _select_range(page, "A1")
    await page.keyboard.press("Meta+A")


async def _write_clipboard(page: Page, text: str) -> None:
    try:
        await page.evaluate("(text) => navigator.clipboard.writeText(text)", text)
    except Exception as error:
        raise SetupError(
            "Chrome could not write to the clipboard. Allow clipboard access for "
            "the app and retry."
        ) from error


async def _copy_range(page: Page, cell_range: Optional[str]) -> str:
    """Copy a range (or the whole sheet when None) and return it as TSV."""
    # Clear the clipboard first so a failed copy can't return stale contents.
    await _write_clipboard(page, "")
    if cell_range is None:
        await _select_all(page)
    else:
        await _select_range(page, cell_range)
    for _ in range(3):
        await page.keyboard.press("Meta+C")
        try:
            text = await page.evaluate("() => navigator.clipboard.readText()")
        except Exception as error:
            raise SetupError(
                "Chrome could not read the selected sheet cells from the clipboard. "
                "Allow clipboard access for the app and retry."
            ) from error
        if text:
            return text
        await page.wait_for_timeout(250)
    return ""


async def _paste_text(page: Page, text: str) -> None:
    await _write_clipboard(page, text)
    await _select_all(page)
    await page.keyboard.press("Backspace")
    await _select_range(page, "A1")
    # A synthetic Meta+V doesn't reach Sheets' paste handler; Edit → Paste reads the
    # clipboard itself. Fall back to the shortcut if the menu isn't available.
    try:
        await page.locator("#docs-edit-menu").click()
        item = page.locator('[role="menuitem"]:has-text("Paste"):not(:has-text("special"))')
        await item.first.click(timeout=5_000)
    except Exception:
        await page.keyboard.press("Escape")
        await page.keyboard.press("Meta+V")


async def _verify_paste(page: Page, expected: list[str], column_index: int) -> None:
    """Wait until the given column holds exactly the expected values."""
    column = _column_name(column_index + 1)
    cell_range = f"{column}1:{column}{len(expected) + 1}"
    for _ in range(PASTE_CHECK_ATTEMPTS):
        await page.wait_for_timeout(500)
        values = [line.strip() for line in (await _copy_range(page, cell_range)).splitlines()]
        while values and not values[-1]:
            values.pop()
        if values == expected:
            return
    raise SetupError(
        "The export did not paste into the sheet completely. No baseline was saved; "
        "retry the export."
    )


def _run(coro):
    try:
        return asyncio.run(coro)
    except SetupError:
        raise
    except Exception as error:
        raise SetupError(f"Google Sheets browser operation failed: {error}") from error


async def _with_sheet(sheet_url: str, operation):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(CDP_URL)
        try:
            page = await _open_sheet(browser, sheet_url)
            return await operation(page)
        finally:
            await browser.close()


def replace_sheet(sheet_url: str, header: list[str], rows: list[list[str]],
                  key_index: int) -> str:
    """Replace the worksheet contents, then confirm the key column landed intact."""
    text = _clipboard_tsv(header, rows)
    expected_keys = [header[key_index], *(row[key_index] for row in rows)]

    async def operation(page: Page):
        await _paste_text(page, text)
        await _verify_paste(page, expected_keys, key_index)
        return await _tab_title(page)

    return _run(_with_sheet(sheet_url, operation))


def read_sheet(sheet_url: str) -> tuple[list[str], list[dict[str, str]], str]:
    """Read the header and data rows. Blank rows are kept so row numbers match the sheet.

    An empty worksheet returns no header and no rows.
    """

    async def operation(page: Page):
        clipboard = await _copy_range(page, None)
        values = list(csv.reader(io.StringIO(clipboard), delimiter="\t"))
        while values and not any(values[-1]):
            values.pop()
        if not values or not any(values[0]):
            return [], [], await _tab_title(page)
        header = [value.strip() for value in values[0]]
        rows = [
            {name: (row[index] if index < len(row) else "")
             for index, name in enumerate(header)}
            for row in values[1:]
        ]
        return header, rows, await _tab_title(page)

    return _run(_with_sheet(sheet_url, operation))
