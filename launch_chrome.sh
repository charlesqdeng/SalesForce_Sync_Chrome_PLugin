#!/usr/bin/env bash
# Launch a dedicated Chrome instance with remote debugging on :9222.
# Uses an isolated profile at ~/chrome-sf-debug so it doesn't disturb your
# regular Chrome. Log into Salesforce once here — the profile persists.

set -euo pipefail

PORT=9222
PROFILE_DIR="$HOME/chrome-sf-debug"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOGIN_URL="${SALESFORCE_LOGIN_URL:-https://login.salesforce.com/}"
EXTENSION_ID="nmikjpppphnlclgjboaiblbpickjlmde"

# Chrome 137+ ignores --load-extension, so the extension must be added once with
# "Load unpacked". Chrome then remembers it in this profile.
check_extension() {
    if grep -qs "$EXTENSION_ID" "$PROFILE_DIR/Default/Preferences" \
            "$PROFILE_DIR/Default/Secure Preferences"; then
        echo "   Open the extension from Chrome's Extensions menu; its popup cannot be opened as a normal tab."
        return
    fi
    echo "⚠️  The extension is not installed in this Chrome profile yet. One-time setup:"
    echo "   1. In the chrome://extensions tab that just opened, turn on Developer mode (top right)."
    echo "   2. Click \"Load unpacked\" and choose: ${SCRIPT_DIR}/extension"
    echo "   3. Pin \"Salesforce Report to Sheets\" from Chrome's Extensions (puzzle) menu."
    curl -s --max-time 5 -X PUT "http://localhost:${PORT}/json/new?chrome://extensions" >/dev/null || true
    printf '%s' "${SCRIPT_DIR}/extension" | pbcopy 2>/dev/null \
        && echo "   (The extension folder path is on your clipboard.)"
}

if curl -s --max-time 2 "http://localhost:${PORT}/json/version" >/dev/null 2>&1; then
    echo "✅ Chrome is already running on :${PORT}."
    PAGES="$(curl -s --max-time 2 "http://localhost:${PORT}/json/list")"
    if ! printf '%s' "$PAGES" | grep -q '"type": *"page"'; then
        echo "   No open windows — opening ${LOGIN_URL}"
        curl -s --max-time 5 -X PUT "http://localhost:${PORT}/json/new?${LOGIN_URL}" >/dev/null
        osascript -e 'tell application "Google Chrome" to activate' >/dev/null 2>&1 || true
    fi
    check_extension
    exit 0
fi

if [ ! -x "$CHROME" ]; then
    echo "❌ Chrome not found at: $CHROME"
    exit 1
fi

echo "🚀 Launching Chrome on :${PORT} with profile ${PROFILE_DIR}..."
"$CHROME" \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="${PROFILE_DIR}" \
    --load-extension="${SCRIPT_DIR}/extension" \
    "${LOGIN_URL}" \
    >/dev/null 2>&1 &

# Wait up to 10s for the debug port to come up.
for _ in $(seq 1 20); do
    if curl -s --max-time 1 "http://localhost:${PORT}/json/version" >/dev/null 2>&1; then
        echo "✅ Chrome is up on :${PORT}."
        echo "   → Complete Salesforce login in the new window if prompted."
        osascript -e 'tell application "Google Chrome" to activate' >/dev/null 2>&1 || true
        check_extension
        exit 0
    fi
    sleep 0.5
done

echo "⚠️  Chrome was launched but :${PORT} didn't come up within 10s."
echo "   Check that Chrome opened; then try: curl -s http://localhost:${PORT}/json/version"
exit 1
