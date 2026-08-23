#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

if [[ -x /opt/homebrew/bin/tesseract ]]; then
  export TESSERACT_CMD="/opt/homebrew/bin/tesseract"
elif [[ -x /usr/local/bin/tesseract ]]; then
  export TESSERACT_CMD="/usr/local/bin/tesseract"
elif command -v tesseract >/dev/null 2>&1; then
  export TESSERACT_CMD="$(command -v tesseract)"
else
  echo "Warning: Tesseract not found. OCR will be disabled until you run:"
  echo "  brew install tesseract tesseract-lang"
fi

export ENVIRONMENT=local
export RESET_DB_ON_START="${RESET_DB_ON_START:-false}"
mkdir -p data

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [[ -z "${LAN_IP}" ]]; then
  LAN_IP="$(python - <<'PY'
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.connect(("8.8.8.8", 80))
    print(sock.getsockname()[0])
except OSError:
    print("127.0.0.1")
finally:
    sock.close()
PY
)"
fi

echo
echo "┌──────────────────────────────────────────────────────────┐"
echo "│  BeanNote local · scan to open on your phone             │"
echo "│  http://${LAN_IP}:8501"
echo "│  http://127.0.0.1:8501"
echo "│  Host 0.0.0.0:8501 · CORS * · DB wiped on startup        │"
echo "└──────────────────────────────────────────────────────────┘"
echo "Tesseract: ${TESSERACT_CMD:-not found}"
echo
python - <<PY
try:
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data("http://${LAN_IP}:8501")
    qr.make(fit=True)
    qr.print_ascii(invert=True)
except Exception:
    pass
PY
echo
exec uvicorn main:app --host 0.0.0.0 --port 8501 --reload
