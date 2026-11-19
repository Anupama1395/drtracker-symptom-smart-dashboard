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
| Parse reports that have no events yet (first run also generates the synthetic data) | `python pipeline_runner.py` |
| Same, at most 3 reports (quick LLM check) | `python pipeline_runner.py 3` |
| Re-run the parser on **unreviewed** output only (e.g. after changing model or prompt) | `python pipeline_runner.py --reparse` |
| Rebuild the synthetic dataset from scratch (asks before deleting uploads, reviews or corrections) | `python pipeline_runner.py --generate` |
| Import a folder of notes (safe to re-run; duplicates are skipped) | `python bulk_import.py docs/samples/generated` |
| Make 100 more synthetic notes (P108–P207) | `python docs/tools/make_synthetic_docs.py --start 108 --count 100` |
| Use a different local model | `SYMPTOMLENS_MODEL=qwen2.5:7b python pipeline_runner.py --reparse` |
| SQL queries | `python Queries.py` |
| Parser accuracy (gold set, clinician corrections, silver labels) | `python evaluate.py` |
| Compare local models, writing `docs/MODEL_COMPARISON.md` | `python evaluate.py --models llama3.1:8b qwen2.5:7b mistral` |
| Write every table to readable CSVs, before committing `data/` | `python export_csv.py` |
| Tests (no LLM needed; GitHub Actions runs them on every push) | `python -m pytest` |
| Preview a FHIR import | `python importer.py samples/fhir_bundle_P006.json` |

## Troubleshooting

| Problem | Fix |
|---|---|
| `Address already in use` | Another copy is on 8501: use `./scripts/restart.sh`, or `PORT=8502 ./scripts/run.sh` |
| "No parsed events yet" in the app | Run `python pipeline_runner.py` |
| Every item is flagged "LLM unavailable" / "Demo mode" banner | Ollama isn't running or the model isn't pulled (`ollama pull llama3.1:8b`). The app still works through the regex fallback. Once it's up, run `python pipeline_runner.py --reparse` |
| "Enter your name as reviewer first" | Type a name in the sidebar's **Reviewer name** box. Every decision is attributed |
| A note was "skipped (duplicate)" | The same patient, date and text is already stored. Nothing was lost |
| Restart didn't happen at 01:22 | Run `./scripts/schedule_restart.sh status`, then check `logs/schedule.log` and `logs/restart.log` |
