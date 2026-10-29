#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating the app's Python environment..."
    python3 -m venv venv
fi

VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"

if ! "$VENV_PYTHON" -c \
    "import certifi, playwright, simple_salesforce" \
    >/dev/null 2>&1; then
    echo "Installing required Python packages..."
    "$VENV_PYTHON" -m pip install playwright certifi simple-salesforce
fi

CERT_PATH=$("$VENV_PYTHON" -c "import certifi; print(certifi.where())")
export SSL_CERT_FILE="$CERT_PATH"
export REQUESTS_CA_BUNDLE="$CERT_PATH"
export CURL_CA_BUNDLE="$CERT_PATH"

echo "Starting the local extension helper. Keep this terminal open."
exec "$VENV_PYTHON" "$SCRIPT_DIR/local_helper.py"
