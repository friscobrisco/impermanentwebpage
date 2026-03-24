# Impermanent Leaderboard Dashboard — Technical Documentation

## Overview

A visualization dashboard for the Impermanent time-series forecasting benchmark. Evaluation results are stored as a Parquet file in S3, fetched by a Python pipeline, and rendered into a single static HTML file with interactive Chart.js visualizations.

**Stack:** Python data pipeline (`fetch_data.py` → `generate_html.py`) producing a single `index.html`, Chart.js (CDN), Inter font (Google Fonts), CSS custom properties for theming
**Data source:** `s3://impermanent-benchmark/v0.1.0/gh-archive/evaluations/evaluation_results.parquet`
**Data shape:** 12 models × 4 subdatasets × 3 frequencies × N sparsity levels × ~30 cutoff dates × 2 metrics
**Theme:** White + violet accents (light/dark mode)

---

## 1. Data Source & Pipeline

### S3 Parquet File

```
s3://impermanent-benchmark/v0.1.0/gh-archive/evaluations/evaluation_results.parquet
```

The parquet file contains evaluation results with the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `subdataset` | string | `issues_opened`, `prs_opened`, `pushes`, `stars` |
| `frequency` | string | `daily`, `monthly`, `weekly` |
| `sparsity_level` | string | Sparsity bucket (from source data); if the column is absent in parquet, the pipeline uses `low` |
| `cutoff` | string | Date identifier, e.g. `2026-01-04` or `2026-01-04-00` |
| `metric` | string | `mase` or `scaled_crps` |
| `model_alias` | string | One of the 12 model names |
| `value` | float | The metric score |

### Pipeline Steps

#### Step 1: `scripts/fetch_data.py` — Fetch & Transform

1. **Download** the parquet file from S3 using `boto3`
2. **Normalize cutoffs** — append `-00` to date-only cutoff strings for consistency
3. **Filter to last 3 months** — only keep cutoffs within 90 days of the latest date
4. **Clamp extreme values** — values with `abs(v) >= 1e6` are set to `0` (handles scientific notation outliers)
5. **Build records** — restructure flat rows into `{subdataset, frequency, sparsity_level, cutoff, values: {model: score}}` objects for both MASE and SCALED_CRPS
6. **Compute summary** — calculate per-model average metrics and average ranks, assign medal emojis to top 3
7. **Write** `data/leaderboard.json`

**Dependencies:** `boto3`, `pandas`, `pyarrow` (see `requirements.txt`)

**Environment variables required:**
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION` (defaults to `us-east-1`)

#### Step 2: `scripts/generate_html.py` — Generate HTML

1. **Read** `data/leaderboard.json`
2. **Read** `templates/dashboard.html`
3. **Inject data** — replace the `/* __DATA_PLACEHOLDER__ */` marker in the template with `const DATA = <compact JSON>;`
4. **Write** `index.html`

### GitHub Actions Workflow

`.github/workflows/update-leaderboard.yml` runs the full pipeline:

- **Schedule:** Every Sunday at 23:00 UTC
- **Trigger:** Manual via `workflow_dispatch`
- **Steps:** Checkout → Setup Python 3.12 → Install deps → Fetch S3 data → Generate HTML → Commit changes → Deploy to GitHub Pages

---

## 2. Data Structure (Embedded in HTML)

All data from the pipeline is embedded as a single `DATA` object in `index.html`:

```javascript
const DATA = {
  models: ["AutoARIMA", "AutoCES", "AutoETS", "Chronos",
           "DynamicOptimizedTheta", "HistoricAverage", "Moirai",
           "Prophet", "SeasonalNaive", "TiRex", "TimesFM", "ZeroModel"],
  cutoffs: ["2026-01-01-00", "2026-01-04-00", ..., "2026-03-14-00"],
  subdatasets: ["issues_opened", "prs_opened", "pushes", "stars"],
  frequencies: ["daily", "monthly", "weekly"],
  sparsity_levels: ["low", "medium", "high", ...],  // pipeline/UI order
  mase: [
    {subdataset: "issues_opened", frequency: "daily", sparsity_level: "low", cutoff: "2026-01-04-00",
     values: {AutoARIMA: 0.426, AutoCES: 0.323, ...}},
    ...
  ],
  crps: [
    // Same structure, SCALED_CRPS values
  ],
  summary: [
    {model: "🥇 ZeroModel", avg_mase: 0.043, avg_crps: 0.517, rank_mase: 1.5, rank_crps: 1.0},
    {model: "🥈 Moirai",    avg_mase: 0.163, avg_crps: 1.170, rank_mase: 3.2, rank_crps: 2.8},
    ...
  ]
}
```

**Note:** ZeroModel is a baseline model. It is included in the data but excluded from the summary cards (top 3 podium) and championship standings in the frontend.

---

## 3. Formulas & Computations

### 3.1 Average Rank Over Time (`computeRankOverTime`)

For each cutoff date, within each (subdataset, frequency) group:

1. Take all 12 model values for that specific (subdataset, frequency, cutoff) row
2. Sort ascending (lower metric = better)
3. Assign ranks 1–12 (1 = best)
4. Average each model's rank across all (subdataset, frequency) groups sharing that cutoff

```
For cutoff C:
  For each (subdataset, frequency) row at cutoff C:
    Sort models by value ascending
    Assign rank_i = position (1-based)

  avgRank(model, C) = Σ rank_i(model) / count(rows at C)
```

The "Avg Rank" view on the main chart inverts the Y-axis (1 at top, 12 at bottom) since lower rank is better.

---

### 3.2 Raw Value Over Time (`computeRawOverTime`)

For each (model, cutoff), average the raw metric value across all matching (subdataset, frequency) groups.

```
rawAvg(model, C) = Σ value(model, subdataset, frequency, C) / count(rows at C)
```

**Outlier handling (CRPS only):** When the max raw CRPS value exceeds 100, values are capped at the 95th percentile × 1.1 (minimum cap of 10) to prevent extreme outliers from compressing the chart.

---

### 3.3 Championship Points (`computeChampionshipPoints`)

A Formula 1-style points system applied to weekly rankings. **ZeroModel is excluded** from this computation.

1. For each cutoff (week):
   a. Compute average rank across all (subdataset, frequency) groups (same as §3.1)
   b. Re-rank the average ranks to get clean 1–11 positions
   c. Award points: `points = N_models - position_index` (11 for 1st, 10 for 2nd, ..., 1 for 11th)
2. Accumulate points cumulatively across weeks

---

### 3.4 Championship Standings Table (`renderChampStandings`)

**ZeroModel is excluded.** Derived from the championship points computation:

| Column | Formula |
|--------|---------|
| **Total Pts** | `cumPoints[model][lastCutoff]` — final cumulative total |
| **Avg Rank** | `N_models + 1 - (totalPts / N_cutoffs)` — reverse-engineered from avg points per week |
| **Best Wk** | `max(weeklyPts)` where `weeklyPts[i] = cumPoints[i] - cumPoints[i-1]` |
| **Worst Wk** | `min(weeklyPts)` |
| **Points Bar** | `width% = (model_totalPts / leader_totalPts) × 100` |

---

### 3.5 Rank Stability Heatmap (`renderHeatmap`)

Same average-rank-per-cutoff computation as §3.1, displayed as a color-coded grid.

**Color gradient** (5-stop, interpolated):
```
Rank 1  (best)  → #4527A0 (deep violet)
Rank ~3         → #7C4DFF (violet)
Rank ~6         → #D1B3FF (lavender)
Rank ~9         → #FF8A65 (coral)
Rank 12 (worst) → #D32F6B (rose)
```

**Row sorting:** Models sorted by overall average rank across all cutoffs (best first).

---

### 3.6 Summary Cards

The top 3 models are displayed as podium cards with gold/silver/bronze medals. **ZeroModel is excluded** — the cards show the top 3 non-baseline models by combined average rank (average of MASE rank and CRPS rank).

---

## 4. Chart Configuration

### Chart Colors (12 distinct, maximally separated)

```javascript
const CHART_COLORS = [
  '#7C4DFF',  // violet        — AutoARIMA
  '#E040FB',  // magenta       — AutoCES
  '#1B5E20',  // forest green  — AutoETS
  '#FF6D00',  // vivid orange  — Chronos
  '#D32F6B',  // rose          — DynamicOptimizedTheta
  '#00897B',  // teal          — HistoricAverage
  '#4527A0',  // deep purple   — Moirai
  '#F9A825',  // amber         — Prophet
  '#0277BD',  // blue          — SeasonalNaive
  '#6D4C41',  // brown         — TiRex
  '#B388FF',  // light violet  — TimesFM
  '#E65100'   // burnt orange  — ZeroModel
];
```

Colors are assigned by model index in `DATA.models` and stay consistent across all charts.

### Chart.js Options (shared across main chart & championship chart)

- Line tension: `0.3` (slight curve)
- Point radius: `3` (hover: `6`)
- Line width: `2` (championship: `2.5`)
- `spanGaps: true` (connects across missing data)
- Interaction mode: `index` (tooltip shows all models at cursor's x position)
- Tooltip: custom styled (white/violet bg, rounded corners, Inter font)

---

## 5. Theming

### CSS Custom Properties

The dashboard uses CSS custom properties with a `data-theme` attribute on `<html>`:

#### Light Mode (default)
```css
--color-bg: #FFFFFF;
--color-surface: #F8F6FC;
--color-primary: #7C4DFF;
--color-text: #1E1333;
--color-text-muted: #4A3D6B;
```

#### Dark Mode
```css
--color-bg: #110D1E;
--color-surface: #1A1330;
--color-primary: #B388FF;
--color-text: #E8E0F7;
--color-text-muted: #CEBEFF;
```

Theme toggle re-renders all 4 visual sections: main chart, championship chart, championship standings, and heatmap.

---

## 6. Controls & State

### State Object
```javascript
let state = {
  metric: 'mase',       // mase | crps — main chart + table
  view: 'rank',         // rank | raw — main chart
  subdataset: 'all',    // all | issues_opened | prs_opened | pushes | stars
  frequency: 'all',     // all | daily | monthly | weekly
  sparsity_level: 'low', // all | …values from DATA.sparsity_levels (defaults to low)
  sortCol: null,        // table column sort
  sortDir: 'asc'        // asc | desc
};
let champMetric = 'mase';    // separate toggle for championship section
let heatmapMetric = 'mase';  // separate toggle for heatmap
```

### Control → Render Wiring

| Control | Affects |
|---------|---------|
| Metric pill (main) | `renderChart()` + `renderTable()` |
| View pill (rank/raw) | `renderChart()` + `renderTable()` |
| Dataset dropdown | `renderChart()` + `renderTable()` |
| Frequency dropdown | `renderChart()` + `renderTable()` |
| Sparsity dropdown | `renderChart()` + `renderTable()` |
| Champ metric pill | `renderChampChart()` + `renderChampStandings()` |
| Heatmap metric pill | `renderHeatmap()` |
| Theme toggle | All of the above |
| Table column header click | `renderTable()` (sort toggle) |

---

## 7. Dashboard Sections

1. **Summary Cards** — Top 3 non-baseline models with gold/silver/bronze medals, avg MASE and avg CRPS
2. **Model Performance Over Time** — Line chart with metric/view/dataset/frequency/sparsity controls (all 12 models including ZeroModel)
3. **Championship Points Race** — Cumulative F1-style points line chart with own metric toggle (ZeroModel excluded)
4. **Championship Standings** — Leaderboard table with rank medals, total points, avg rank, best/worst week, and color-coded points bars (ZeroModel excluded)
5. **Rank Stability Heatmap** — Color-coded grid of average rank per model per cutoff week
6. **Detailed Results** — Sortable table showing raw metric values for the latest cutoff date per filter

---

## 8. File Structure

```
impermanentwebpage/
├── .github/workflows/
│   └── update-leaderboard.yml    # GitHub Actions: weekly S3 fetch + deploy
├── scripts/
│   ├── fetch_data.py             # Download parquet from S3 → data/leaderboard.json
│   └── generate_html.py          # Inject JSON into template → index.html
├── templates/
│   └── dashboard.html            # HTML template with /* __DATA_PLACEHOLDER__ */
├── data/
│   ├── evaluation_results.parquet # Raw evaluation data (local copy)
│   └── leaderboard.json          # Processed JSON for the dashboard
├── index.html                    # Generated output (committed by CI)
├── requirements.txt              # boto3, pandas, pyarrow
├── tc.png                        # TimeCopilot logo
└── tcwh.png                      # TimeCopilot logo (white variant)
```

**External dependencies (CDN):**
- `chart.js@4.4.7` — charting library
- `Inter` font — Google Fonts
