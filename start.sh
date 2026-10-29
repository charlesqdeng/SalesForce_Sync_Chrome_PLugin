#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Salesforce ↔ Google Sheets..."
./launch_chrome.sh
echo "Starting the local helper. Keep this terminal open while using the extension."
exec ./run.sh
