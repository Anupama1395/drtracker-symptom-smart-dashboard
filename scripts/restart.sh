#!/bin/bash
# Stop whatever is serving port 8501 (the dashboard) and start a fresh copy in the background.
# Used by the daily 1:22 AM schedule; you can also run it by hand: ./scripts/restart.sh
# Log: logs/symptomlens.log   Restart history: logs/restart.log
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8501}"
mkdir -p logs
echo "$(date '+%Y-%m-%d %H:%M:%S') restart requested" >> logs/restart.log

OLD=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$OLD" ]; then
  kill $OLD 2>/dev/null
  for _ in $(seq 1 10); do lsof -ti tcp:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 1; done
  lsof -ti tcp:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && kill -9 $OLD 2>/dev/null
  echo "$(date '+%Y-%m-%d %H:%M:%S') stopped pid(s) $OLD" >> logs/restart.log
fi

nohup ./scripts/run.sh >> logs/symptomlens.log 2>&1 &
NEW=$!
for _ in $(seq 1 30); do curl -s "http://127.0.0.1:$PORT/_stcore/health" | grep -q ok && break; sleep 1; done
if curl -s "http://127.0.0.1:$PORT/_stcore/health" | grep -q ok; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') started pid $NEW, healthy on port $PORT" >> logs/restart.log
  echo "SymptomLens running at http://localhost:$PORT (pid $NEW)"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') started pid $NEW but health check failed, see logs/symptomlens.log" >> logs/restart.log
  echo "Started but not healthy yet: check logs/symptomlens.log" >&2
  exit 1
fi
