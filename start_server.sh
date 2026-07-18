
#!/bin/bash
set -euo pipefail

BACKEND_DIR="${TIA_BACKEND_DIR:-$(cd "$(dirname "$0")" && pwd)}"
cd "$BACKEND_DIR"

exec "$BACKEND_DIR/venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port 8000
