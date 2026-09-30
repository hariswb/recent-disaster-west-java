# recentdisaster: Pantau Insiden Jabar

This is a zero-cost monitor of incidents in West Java (Jawa Barat) over the **last 24 hours**. It covers:
- natural hazards: flood, landslide, earthquake, whirlwind, drought, volcano
- fires, including forest and land fires (karhutla)
- social incidents: MBG (Makan Bergizi Gratis) school-meal poisoning, other food/miras/gas poisoning, outbreaks, bullying (perundungan), sexual violence, violence against children and KDRT, tawuran and clashes, intolerance and social conflict, human trafficking (TPPO) and abduction. Suicide cases and generic adult crime are deliberately excluded.
- infrastructure failures and accidents: collapsed bridges, roads or buildings, outages, and accidents

It runs on a GitHub Actions cron schedule, pulls in regional news feeds, and extracts structured facts. The results are published as a static dashboard on GitHub Pages.

```
fetch (RSS | WP JSON | Google News | HTML) → normalize → 24h window → dedupe
 → rule prefilter (taxonomy + Jabar gazetteer) → fetch article body (candidates only)
 → LLM extraction via rotating free providers (cached by URL) ─fallback→ rules
 → cluster reports of the same event → docs/data/incidents.json → dashboard
```

For each incident the tool extracts:
- category and subcategory
- kab/kota, kecamatan and desa, with map coordinates from the gazetteer centroids
- victims: dead, injured, missing, displaced, affected, and damaged houses
- affected institutions (school, pesantren, market, village, and so on)
- a short summary and every source link

## LLM providers

Only free tiers are used. Each provider is configured in `config/llm_providers.yaml`, and a provider becomes active only when its key is present.

| Provider | Status | Secret | Models (in fallback order) | Notes |
|---|---|---|---|---|
| Groq | **active** | `GROQ_API_KEY` | `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` | Per-model limits: 8k tokens/min and 1k requests/day. The pool switches models on per-minute 429s. |
| OpenRouter | **active** | `OPENROUTER_API_KEY` | `qwen/qwen3.8-27b:free`, `google/gemma-4-31b-it:free`, `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-26b-a4b-it:free` | `model_allow: ":free$"`: the code refuses any model without the `:free` suffix. Gemma is often rate-limited upstream. |
| GLM (Z.ai) | **active** | `GLM_API_KEY` | `glm-4.7-flash`, `glm-4.5-flash` | `model_allow: "-flash$"`: only the free Flash tier (`-flashx` and the rest are paid). Thinking is disabled. |
| Gemini | **active, flaky** | `GEMINI_API_KEY` | `gemini-flash-latest`, `gemini-flash-lite-latest` | Flash often returns 503 "high demand". The pool rests it for 2 minutes and uses Flash-Lite. |
| GitHub Models | active in Actions only | none (built-in `GITHUB_TOKEN`) | `openai/gpt-4.1-mini`, `openai/gpt-4o-mini`, … | Needs `permissions: models: read` (already set in the workflow). Locally, it needs a PAT in `.env`. |
| Cerebras, Mistral | configured, no key | `CEREBRAS_API_KEY`, `MISTRAL_API_KEY` | see config | Add a key to activate. |

Model availability was last verified on 2026-09-30. Run `uv run recentdisaster llm-check` to re-check; failures print the provider's current model list.

## Secrets

Secrets are never committed.

- **Locally:** copy `.env.example` to `.env` and fill in the keys. `.env` is gitignored, and the CLI loads it automatically; variables already set in your shell take precedence.
- **Production (GitHub Actions):** use repository secrets. The workflow passes them to the run as environment variables.

### Adding the GitHub secrets

With the web UI:

1. Open the repository on GitHub, then go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret** once per key. The name must match exactly:
   - `GROQ_API_KEY`
   - `OPENROUTER_API_KEY`
   - `GLM_API_KEY`
   - `GEMINI_API_KEY`
   - (optional) `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`
3. Paste the value. Secrets are write-only: GitHub never shows them again, and it masks them in logs.

Don't create `GITHUB_TOKEN`; Actions provides it automatically.

Or with the GitHub CLI, from the repo root, reading the values from your local `.env`:

```bash
gh secret set -f .env          # uploads every KEY=value line in .env as a repo secret
gh secret list                 # verify (names only)
```

(`gh secret set -f` skips comment lines. Remove any `GITHUB_TOKEN=` line from `.env` first, because GitHub rejects secret names that start with `GITHUB_`.)

To rotate a key, set the same secret name again. The next run uses the new value.

## Setup (one time)

1. Push the repo to GitHub as a **public** repository. Public repos get free Pages and unlimited Actions minutes.
2. Go to **Settings → Pages → Source: GitHub Actions**.
3. Add the LLM secrets (see [Adding the GitHub secrets](#adding-the-github-secrets)). GitHub Models works with no key at all.
4. Go to **Actions → monitor → Run workflow** to run it the first time. After that it runs at 06:00, 10:00, 14:00 and 18:00 WIB.

GitHub pauses scheduled workflows after 60 days without repository activity. The data commits normally keep the repo active, but if a pause happens, re-enable the workflow from the Actions tab.

## Local use

```bash
uv sync
cp .env.example .env                           # then fill in your keys
uv run recentdisaster run --no-llm --dry-run   # rules only, writes nothing, prints candidates
uv run recentdisaster run                      # full run (uses the keys in .env / env)
uv run recentdisaster llm-check                # test each provider; lists models on failure
uv run recentdisaster sources                  # fetch every source (incl. disabled) and report
uv run pytest
python -m http.server -d docs                  # preview the dashboard
```

## Configuration

| File | Purpose |
|---|---|
| `config/sources.yaml` | News sources. `type: rss / wp_json / gnews / html`. HTML sources use a `link_pattern`; only link text that looks like an incident gets fetched. |
| `config/taxonomy.yaml` | Categories and Indonesian keyword regexes, title exclusions (simulasi, sosialisasi, …), and entity words. |
| `config/llm_providers.yaml` | The free LLM pool: OpenAI-compatible `base_url`, ordered `models`, `rpm`, per-run caps. |
| `data/gazetteer/jabar.json` | 27 kab/kota and 627 kecamatan with centroids. Rebuild it with `uv run python scripts/build_gazetteer.py`. |

### LLM rotation

Every provider is called through the OpenAI-compatible `/chat/completions` API:
- Calls rotate round-robin across the providers that have a key set.
- On a 429 or quota error, that provider is skipped for the rest of the run.
- An unknown model moves on to the next entry in `models`.
- Invalid JSON is retried once on the next provider.
- When the pool is exhausted, the rules extraction is used and the incident is tagged `aturan` on the dashboard.
- Rule-only results get retried with an LLM on the next run.

Free-tier model names change often. If `llm-check` fails, it prints the provider's available models so you can update `models:`.

### State

`data/cache/processed.json` holds per-URL results for 48 hours. An article is only sent to an LLM once, and incidents stay on the dashboard for 24 hours even after the article drops out of its feed. The workflow commits this file together with `docs/data/incidents.json`.

## Known limitations

- Map points are kecamatan or kab/kota centroids, not exact locations. Incidents described only as "di Jabar" appear in the list, not on the map.
- Rules-only extraction (when no LLM is available) is noisy: recaps and opinion pieces can slip through, and victim counts are approximate ("puluhan" is counted as 20).
- Radar Bogor and Pemkab Bogor render their article links with JavaScript and are disabled. Google News covers Bogor partially.
- Google News items have no article body (redirect links), so they are classified from the headline only.

Region data: [cahyadsn/wilayah_boundaries](https://github.com/cahyadsn/wilayah_boundaries) (MIT). Map © OpenStreetMap contributors.
