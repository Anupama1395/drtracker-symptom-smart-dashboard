# How to run SymptomLens

All commands run from the project folder:

```bash
cd /path/to/Pipeline_securities
```

## 1. One-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x scripts/*.sh
```

Optional, for the LLM parser: install [Ollama](https://ollama.com), open it, then run `ollama pull llama3.1:8b`. Without it, everything still works: reports go through the regex fallback and are flagged for review.

## 2. Start the dashboard

```bash
./scripts/run.sh
```

Open **http://localhost:8501**. Press `Ctrl+C` to stop it.

To run it in the background instead, or to restart a copy that's already running:

```bash
./scripts/restart.sh
```

This stops whatever is serving port 8501 and starts a fresh copy in the background. It waits until the app answers before reporting success.
- App output: `logs/symptomlens.log`
- Restart history: `logs/restart.log`

To stop a background copy:

```bash
kill $(lsof -ti tcp:8501 -sTCP:LISTEN)
```

## 3. Automatic restart every day at 1:22 AM

```bash
./scripts/schedule_restart.sh install     # turn on
./scripts/schedule_restart.sh status      # check: ON / OFF
./scripts/schedule_restart.sh uninstall   # turn off
```

- **How it works:** a macOS LaunchAgent (`~/Library/LaunchAgents/com.symptomlens.restart.plist`) runs `scripts/restart.sh` daily at 01:22.
- **The Mac must be awake.** If it's asleep at 01:22, macOS runs the restart when it wakes. To guarantee 01:22, schedule a wake in **System Settings → Battery → Options**.
- **Check that it ran:** look in `logs/restart.log` the next morning.
- **Moved the project folder?** Run `install` again, because the schedule stores the full path.

## 4. Other commands

| What | Command |
|---|---|
| Rebuild all data and parse every report (**resets uploads and reviews**) | `python pipeline_runner.py` |
| Parse only the first 3 reports (quick LLM check) | `python pipeline_runner.py 3` |
| SQL queries | `python Queries.py` |
| Parser accuracy (gold set, clinician corrections, silver labels) | `python evaluate.py` |
| Tests (no LLM needed) | `python -m pytest` |
| Preview a FHIR import | `python importer.py samples/fhir_bundle_P006.json` |
| Make 100 synthetic upload documents (P108–P207) | `python docs/tools/make_synthetic_docs.py --start 108 --count 100` |

## Troubleshooting

| Problem | Fix |
|---|---|
| `Address already in use` | Another copy is on 8501: use `./scripts/restart.sh`, or `PORT=8502 ./scripts/run.sh` |
| "No parsed events yet" in the app | Run `python pipeline_runner.py` |
| Every item is flagged "LLM unavailable" | Ollama isn't running or the model isn't pulled. The app still works through the regex fallback |
| Restart didn't happen at 01:22 | Run `./scripts/schedule_restart.sh status`, then check `logs/schedule.log` and `logs/restart.log` |
