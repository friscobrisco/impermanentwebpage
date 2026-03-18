# Impermanent Leaderboard Dashboard — Technical Documentation

## Overview

A custom visualization dashboard built on top of the [TimeCopilot/ImpermanentLeaderboard](https://huggingface.co/spaces/TimeCopilot/ImpermanentLeaderboard) HuggingFace Space. The original Space uses Gradio 5.x with matplotlib plots. This dashboard extracts the raw data via the Gradio API and re-presents it using Chart.js in a single static HTML file with interactive controls.

**Stack:** Single `index.html` file, Chart.js (CDN), Inter font (Google Fonts), CSS custom properties for theming  
**Data:** 12 models × 4 subdatasets × 4 frequencies × 13 cutoff dates × 2 metrics  
**Theme:** White + violet accents (light/dark mode)

---

## 1. Data Source & API Calls

### Base URL

```
https://timecopilot-impermanentleaderboard.hf.space
```

### Request 1 — GET /config (MASE data, free)

```bash
curl 'https://timecopilot-impermanentleaderboard.hf.space/config'
```

**What it returns:** The full Gradio app config (~49KB JSON). Inside the `components` array (22 total), one component of type `dataframe` contains the default results table pre-rendered with MASE values.

**Where the data lives in the response:**
```
response.components[]
  .filter(c => c.component === "dataframe")
  .props.value → { headers: [...], data: [[...], ...] }
```

**Extracted structure:**
| Column | Type | Description |
|--------|------|-------------|
| subdataset | string | `issues_opened`, `prs_opened`, `pushes`, `stars` |
| frequency | string | `daily`, `hourly`, `monthly`, `weekly` |
| cutoff | string | Date identifier, e.g. `2026-01-04-00` |
| AutoARIMA…ZeroModel | float | MASE score for each of the 12 models |

**Dimensions:** 15 columns × 68 rows

**Sample row:**
```json
["issues_opened", "daily", "2026-01-04-00", 0.426, 0.323, 0.403, 0.167, 0.323, 0.794, 0.163, 0.471, 0.173, 0.159, 0.14, 0.099]
```

---

### Request 2 — GET /gradio_api/info (Schema discovery)

```bash
curl 'https://timecopilot-impermanentleaderboard.hf.space/gradio_api/info'
```

**Purpose:** Discover available API endpoints and their parameter schemas.

**Named endpoints found:**
| Endpoint | Returns | Useful? |
|----------|---------|---------|
| `/update_plots` | matplotlib Plot objects | No (images, not data) |
| `/update_plots_1`, `_2` | Same | No |
| `/build_table` | `{headers, data}` Dataframe | **Yes** |
| `/build_table_1`, `_2`, `_3` | Same schema (different tabs) | Redundant |

**`/build_table` parameter schema:**
```
[0] metric:     Literal["mase", "scaled_crps"]                                    default="mase"
[1] subdataset: Literal["All", "issues_opened", "prs_opened", "pushes", "stars"]   default="All"
[2] frequency:  Literal["All", "daily", "hourly", "monthly", "weekly"]             default="All"
[3] models:     list[Literal["AutoARIMA", "AutoCES", ..., "ZeroModel"]]            default=all 12
```

**Return type:**
```json
{
  "headers": ["subdataset", "frequency", "cutoff", "AutoARIMA", ...],
  "data": [["issues_opened", "daily", "2026-01-04-00", 0.426, ...], ...],
  "metadata": null
}
```

---

### Request 3 — POST + GET /gradio_api/call/build_table (SCALED_CRPS data)

This uses the Gradio 5.x queue pattern (two-step: submit → poll).

**Step 1 — Submit:**
```bash
curl -X POST \
  'https://timecopilot-impermanentleaderboard.hf.space/gradio_api/call/build_table' \
  -H 'Content-Type: application/json' \
  -d '{
    "data": [
      "scaled_crps",
      "All",
      "All",
      ["AutoARIMA", "AutoCES", "AutoETS", "Chronos",
       "DynamicOptimizedTheta", "HistoricAverage", "Moirai",
       "Prophet", "SeasonalNaive", "TiRex", "TimesFM", "ZeroModel"]
    ]
  }'
```

**Response:**
```json
{"event_id": "f177351727134a70bf616386253b6db0"}
```

**Step 2 — Fetch result (SSE stream):**
```bash
curl 'https://timecopilot-impermanentleaderboard.hf.space/gradio_api/call/build_table/{event_id}'
```

**Response format (Server-Sent Events):**
```
event: complete
data: [{"headers": ["subdataset", "frequency", "cutoff", ...], "data": [[...], ...]}]
```

Same 15 columns × 68 rows structure as MASE, but with SCALED_CRPS values. Note: some CRPS values are extremely large (e.g. `9,209,777,706` for Moirai on certain cutoffs) due to the nature of the metric.

---

## 2. Data Transformation (Embedded in HTML)

All raw data from both API calls is embedded as a single `DATA` object in the HTML file:

```javascript
const DATA = {
  models: ["AutoARIMA", "AutoCES", "AutoETS", "Chronos",
           "DynamicOptimizedTheta", "HistoricAverage", "Moirai",
           "Prophet", "SeasonalNaive", "TiRex", "TimesFM", "ZeroModel"],
  cutoffs: ["2025-10-01-00", "2025-11-01-00", ..., "2026-02-12-00"],  // 13 dates
  subdatasets: ["issues_opened", "prs_opened", "pushes", "stars"],
  frequencies: ["daily", "hourly", "monthly", "weekly"],
  mase: [
    {subdataset: "issues_opened", frequency: "daily", cutoff: "2026-01-04-00",
     values: {AutoARIMA: 0.426, AutoCES: 0.323, ...}},
    ...
  ],
  crps: [
    // Same structure, SCALED_CRPS values
  ],
  summary: [
    {model: "🥇 TimesFM", avg_mase: 0.171, avg_crps: 1.055},
    {model: "🥈 ZeroModel", avg_mase: 0.143, avg_crps: 1.000},
    {model: "🥉 TiRex",    avg_mase: 0.162, avg_crps: 2.270},
    ...
  ]
}
```

The original flat table rows (68 each for MASE and CRPS) were restructured into objects with a `values` map for easier per-model access.

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

**Example:** If TimesFM gets rank 1 in `issues_opened/daily` and rank 3 in `prs_opened/weekly` at the same cutoff, its average rank for that cutoff = (1+3)/2 = 2.0.

The "Avg Rank" view on the main chart inverts the Y-axis (1 at top, 12 at bottom) since lower rank is better.

---

### 3.2 Raw Value Over Time (`computeRawOverTime`)

Simpler: for each (model, cutoff), average the raw metric value across all matching (subdataset, frequency) groups.

```
rawAvg(model, C) = Σ value(model, subdataset, frequency, C) / count(rows at C)
```

**Outlier handling (CRPS only):** When the max raw CRPS value exceeds 100, values are capped at the 95th percentile × 1.1 (minimum cap of 10) to prevent extreme outliers from compressing the chart.

```javascript
cap = max(percentile95 * 1.1, 10)
displayValue = min(actualValue, cap)
```

---

### 3.3 Championship Points (`computeChampionshipPoints`)

A Formula 1-style points system applied to weekly rankings:

1. For each cutoff (week):
   a. Compute average rank across all (subdataset, frequency) groups (same as §3.1)
   b. Re-rank the average ranks to get clean 1–12 positions
   c. Award points: `points = N_models + 1 - position` (12 for 1st, 11 for 2nd, ..., 1 for 12th)
2. Accumulate points cumulatively across weeks

```
For cutoff C:
  avgRanks = computeAvgRank(allModels, C)                    // from §3.1
  sortedModels = sort(models, by avgRanks ascending)
  For position i (0-based):
    weekPoints(sortedModels[i]) = N_models - i              // 12, 11, 10, ..., 1
  
  cumPoints(model, C) = cumPoints(model, C-1) + weekPoints(model)
```

**Example:** If TimesFM finishes 1st in week 5, it gets 12 points that week. If its cumulative total was 55 after week 4, it's now 67 after week 5.

---

### 3.4 Championship Standings Table (`renderChampStandings`)

Derived from the championship points computation:

| Column | Formula |
|--------|---------|
| **Total Pts** | `cumPoints[model][lastCutoff]` — final cumulative total |
| **Avg Rank** | `N_models + 1 - (totalPts / N_cutoffs)` — reverse-engineered from avg points per week |
| **Best Wk** | `max(weeklyPts)` where `weeklyPts[i] = cumPoints[i] - cumPoints[i-1]` |
| **Worst Wk** | `min(weeklyPts)` |
| **Points Bar** | `width% = (model_totalPts / leader_totalPts) × 100` |

---

### 3.5 Rank Stability Heatmap (`renderHeatmap`)

Same average-rank-per-cutoff computation as §3.1, but displayed as a color-coded grid instead of a line chart.

**Color gradient** (5-stop, interpolated):
```
Rank 1  (best)  → #4527A0 (deep violet)
Rank ~3         → #7C4DFF (violet)
Rank ~6         → #D1B3FF (lavender)
Rank ~9         → #FF8A65 (coral)
Rank 12 (worst) → #D32F6B (rose)
```

**Interpolation:**
```javascript
t = (rank - 1) / (N_models - 1)    // 0 = best, 1 = worst
segment = t * (stops.length - 1)    // which pair of stops
localT = segment - floor(segment)   // interpolation factor within pair
color = lerp(stops[floor], stops[ceil], localT)  // per-channel RGB lerp
```

**Text color:** White for dark cells (t < 0.35 or t > 0.80), dark (#1E1333) for light cells.

**Row sorting:** Models sorted by overall average rank across all cutoffs (best first).

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

Chart.js tooltip/grid colors are hardcoded per-theme in each render function (Chart.js doesn't read CSS variables), checked via:
```javascript
const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
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
  frequency: 'all',     // all | daily | hourly | monthly | weekly
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
| Champ metric pill | `renderChampChart()` + `renderChampStandings()` |
| Heatmap metric pill | `renderHeatmap()` |
| Theme toggle | All of the above |
| Table column header click | `renderTable()` (sort toggle) |

---

## 7. Dashboard Sections

1. **Summary Cards** — Top 3 models (from pre-computed `DATA.summary`) with gold/silver/bronze medals, avg MASE and avg CRPS
2. **Model Performance Over Time** — Line chart with metric/view/dataset/frequency controls
3. **Championship Points Race** — Cumulative F1-style points line chart with own metric toggle
4. **Championship Standings** — Leaderboard table with rank medals, total points, avg rank, best/worst week, and color-coded points bars
5. **Rank Stability Heatmap** — Color-coded grid of average rank per model per cutoff week
6. **Detailed Results** — Sortable table showing raw metric values for the latest cutoff date per filter

---

## 8. File Structure

```
leaderboard-viz/
└── index.html          # Single file (~1400 lines), contains:
                        #   - CSS (design tokens, components, responsive)
                        #   - HTML (header, cards, charts, tables, heatmap)
                        #   - JavaScript (data, computations, rendering, controls)
                        #   - Embedded DATA object (both MASE and CRPS datasets)
```

**External dependencies (CDN):**
- `chart.js@4.4.7` — charting library
- `Inter` font — Google Fonts

No build step, no bundler, no framework. Just open the HTML file.
