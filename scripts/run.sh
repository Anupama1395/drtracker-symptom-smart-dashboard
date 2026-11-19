#!/bin/bash
# Start the SymptomLens dashboard at http://localhost:8501
#   ./scripts/run.sh            -> runs in this terminal (Ctrl+C to stop)
set -euo pipefail
cd "$(dirname "$0")/.."                       # project root, wherever you run this from
source .venv/bin/activate
exec streamlit run app.py --server.port "${PORT:-8501}" --server.headless true
