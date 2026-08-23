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

echo "BeanNote local · ENVIRONMENT=${ENVIRONMENT} · DB=./data/beannote.db"
echo "Tesseract: ${TESSERACT_CMD:-not found}"
echo "Auto-flush: on (local SQLite is wiped on each startup)"
exec uvicorn main:app --reload --host 0.0.0.0 --port 8501
