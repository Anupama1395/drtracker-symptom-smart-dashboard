# Model comparison (2026-11-19)

30 reports: gold.json. Prompt version `v2`, temperature 0, schema-constrained output.

| Model | Precision | Recall | Severity MAE | Frequency exact | Valid JSON | Fallback | Sec/report |
|---|---|---|---|---|---|---|---|
| llama3.1:8b | not run: LLM unavailable (ResponseError); using the regex fallback | | | | | | |
| qwen2.5:7b | not run: LLM unavailable (ResponseError); using the regex fallback | | | | | | |
| mistral | not run: LLM unavailable (ResponseError); using the regex fallback | | | | | | |
| regex baseline | 0.84 | 0.80 | 0.00 | 9/10 | n/a | n/a | <0.01 |

**No model could be run.** Start Ollama and pull the models, then run this again.
