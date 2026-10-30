# Salesforce Report ↔ Google Sheets

A Chrome extension that exports a Salesforce Opportunity report to a Google
Sheet and syncs edited cells back to Salesforce.

## First-time setup

1. Start the app:

   ```sh
   ./start.sh
   ```

   Or double-click **Start Salesforce Sheets.command** in Finder. It opens a
   Terminal window and runs `start.sh` for you.

   The launcher creates the Python environment, installs the Salesforce and
   browser automation dependencies, opens a dedicated Chrome profile
   (`~/chrome-sf-debug`), and starts the local helper. Keep the terminal open
   while using the app.

2. Install the extension in the dedicated Chrome window (one time only).
   Chrome 137 and later ignore the `--load-extension` launch flag, so the
   extension has to be added by hand. If it is missing, `start.sh` prints these
   steps, opens `chrome://extensions`, and copies the extension folder path to
   your clipboard.

   1. On the `chrome://extensions` page, turn on **Developer mode** (top right).
   2. Click **Load unpacked**, press **⌘⇧G**, paste the path, and click
      **Select**. The folder is this project's `extension/` directory, for
      example `/Users/<you>/<path-to-this-repo>/extension`.
   3. Click Chrome's Extensions (puzzle-piece) icon and pin
      **Salesforce Report to Sheets**.

   Chrome remembers the extension in this profile, so you don't need to repeat
   this. The `key` in `extension/manifest.json` keeps the extension ID fixed
   (`nmikjpppphnlclgjboaiblbpickjlmde`), which the local helper requires.

3. Sign in to Salesforce and Google in the dedicated Chrome window if prompted.
   Open **Salesforce Report to Sheets** from the Extensions menu. Paste the
   Salesforce report URL and click its **Save** button, then do the same for
   the default Google Sheet URL. Each URL is saved on its own, so you can save
   one, close the popup to copy the other, and come back.
4. Select **Export report to sheet**. Edit the sheet, then select **Sync sheet
   edits to Salesforce** and confirm the preview.

After setup, `./start.sh` (or double-clicking **Start Salesforce
Sheets.command**) is the only step needed for each session. The
extension, the Salesforce and Google sign-ins, and the configured URLs are
reused.

## Troubleshooting

- **The extension isn't in the Extensions menu.** It hasn't been installed in
  the dedicated profile yet. Make sure you're in the Chrome window that
  `start.sh` opened (not your regular Chrome), then follow step 2 above. Run
  `./launch_chrome.sh` to reopen the Extensions page with the path on your
  clipboard.
- **"Load unpacked" is missing.** Turn on **Developer mode** on
  `chrome://extensions`. If it can't be turned on, a Chrome policy on your
  machine may be blocking developer mode.
- **The extension's ID changed, or the helper returns "Only Chrome extensions
  may access this helper."** Remove the extension and load it again from the
  `extension/` folder. Don't remove the `key` field from `manifest.json`.
- **"Cannot reach the local helper."** `./start.sh` must still be running in a
  terminal.
- **"The export did not paste into the sheet completely."** Export checks the
  pasted Opportunity IDs before saving its local copy. Keep the sheet tab open
  and visible, allow clipboard access if Chrome asks, then export again.

## Sync behavior

- The report must include the Opportunity ID detail column, which is used to
  match rows after sorting.
- Sheets access uses the Google account signed into the dedicated Chrome
  profile. It uses browser clipboard copy/paste; no GCP project, Sheets API,
  OAuth client, or Google credential file is used. Keep the sheet tab selected
  in the configured URL and allow clipboard permissions if Chrome asks.
- All report detail columns are exported. Editable Opportunity fields and
  User lookups can sync back; calculated, grouped, and other related-object
  columns remain read-only.
- Only changed cells are considered. Conflicts are skipped, cleared cells are
  left unchanged, and Salesforce-rejected values are recorded locally and
  included in the sync result.
- **Export report to sheet** does not overwrite unsynced sheet edits. Sync or
  restore them before exporting again.
- **Clear sheet and re-export** clears the whole worksheet and exports the
  report fresh, discarding any unsynced sheet edits. Use it when the sheet is
  out of date or messy and you want to start over. It asks for confirmation
  first and never changes Salesforce.

## Files

| File | Purpose |
|---|---|
| `start.sh` | One-command Chrome and helper launcher |
| `launch_chrome.sh` | Starts the dedicated Chrome profile with the extension |
| `run.sh` | Prepares dependencies and runs the local helper |
| `extension/` | Chrome Manifest V3 popup and configuration |
| `local_helper.py` | Loopback API for extension actions |
| `salesforce_utils.py` | Salesforce session, report, and user lookup helpers |
| `sf_to_sheet.py` | Google Sheets and Opportunity data operations |
| `sheets_browser.py` | Reads and writes Sheets through Chrome clipboard |
| `sheet_sync.py` | Changed-cell detection, conflict checks, and updates |

## Security

- The helper listens only on `127.0.0.1:8765` and accepts requests from this
  extension's ID.
- The dedicated Chrome profile exposes Salesforce's authenticated session on
  local debugging port 9222 while it is running. Use it only for this app and
  close it when finished.
- Sheet baselines and rejected-cell details are stored with owner-only
  permissions under `~/.salesforce-sheet-sync/`.
- Export replaces the active worksheet's cell contents; the extension asks for
  confirmation first. Clipboard automation depends on Google's Sheets UI and
  can require updates if that interface changes.
