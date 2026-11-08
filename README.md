
# Kuku Promo Finder
### AI-Powered Promo Discovery for Microdrama Screenplays

A local Python and Streamlit application that identifies potential **20–60 second promotional cuts** from timestamped microdrama screenplays. It generates candidate windows, evaluates their hooks and cliffhangers, ranks promising cuts, and enables human editorial review.

**Repository:** [kuku-promo-finder-PULKIT_23065119](https://github.com/pulkitgulaticdciv23-sudo/kuku-promo-finder-PULKIT_23065119)

This project uses the open-source Qwen3 model through Ollama for local inference. The documented local workflow does not require a paid model API or API key.

The application analyses screenplay text and exports suggested timestamps. It does not edit, render, or play video.

---

## 1. Problem Statement

Microdrama editors manually watch episodes to identify compelling promotional moments for platforms such as Instagram, TikTok, and YouTube.

This project aims to reduce the manual screening effort by identifying potential promo cuts across multiple screenplay episodes.

A good promo should:

- Open with a compelling hook within the first 3 seconds.
- End at an unresolved moment or cliffhanger.
- Make sense independently to viewers unfamiliar with the show.
- Have a duration between 20 and 60 seconds.

The objective is to identify the right moments and timestamps, not to produce finished promotional videos.

---

## 2. Solution Architecture

The application follows a hybrid Python + LLM pipeline.

### Processing Workflow

1. **Screenplay Parsing:** Extracts timestamped scenes, dialogue, and action events.
2. **Candidate Generation:** Python generates a limited menu of possible 20–60 second promo windows.
3. **Structural Validation:** Checks duration, chronology, timestamps, and source-backed opening evidence.
4. **AI Selection:** Qwen evaluates candidate windows and explains their promotional potential.
5. **AI Judging:** A separate evaluation pass scores structurally accepted candidates.
6. **Scoring and Ranking:** Combines model judgments with code-based features.
7. **Overlap Removal:** Filters overlapping candidates and applies diversity heuristics.
8. **Human Review:** Streamlit allows editors to inspect suggestions and record manual verdicts.

The pipeline also supports bounded cross-episode combinations.

### Scoring Methodology

The default scoring mechanism combines:

- **70% AI-based evaluation**
- **30% code-based features**

The nine evaluation dimensions are:

| Evaluation Dimension | Weight |
|---|---:|
| Hook strength | 0.17 |
| Cliffhanger strength | 0.20 |
| Standalone clarity | 0.15 |
| Emotional intensity | 0.07 |
| Genre fit | 0.05 |
| Spoiler avoidance | 0.06 |
| Speech begins within first 3 seconds | 0.08 |
| Dialogue density | 0.06 |
| Duration near 35–55 seconds | 0.10 |
| Earlier episode preference | 0.06 |
| **Total** | **1.00** |

The weights are configurable through `scoring_weights` and must sum to 1.

The model-based judgments are combined with deterministic features to produce the final ranking.

---

## 3. Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python |
| Application Interface | Streamlit |
| Local LLM Runtime | Ollama |
| AI Model | Qwen3 4B Instruct |
| Model Communication | Local HTTP API |
| Reporting | CSV, JSON, Markdown |

### Verified Local Environment

The following environment was inspected on the developer's Windows PC on 6 November 2026.

| Component | Verified Value |
|---|---|
| Python | 3.13.5 |
| Ollama Runtime | 0.40.15 |
| Model Family | Qwen3 |
| Exact Model Tag | `qwen3:4b-instruct` |
| Parameters | Approximately 4 billion |
| Quantization | Q4_K_M / GGUF |
| Model Download Size | Approximately 2.5 GB |
| Model ID | `0edcdef34593` |
| Model License | Apache License 2.0 |
| Streamlit | 1.67.0 |
| ReportLab | 4.5.1 |

Full installed model digest:

```text
0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
```

Ollama is the runtime; Qwen3 is the model.

The default application configuration is `config.stage4.local.json`.

| Setting | Value |
|---|---|
| Provider | `ollama_local` |
| Model | `qwen3:4b-instruct` |
| Requested Context | 16,384 tokens |
| Maximum Generated Tokens | 3,072 |
| Temperature | 0.7 |
| Request Timeout | 1,800 seconds |
| Request Rate Limit | 60 requests/minute |
| Candidates per Episode | Up to 2 |
| Judge Batch Size | 2 |
| Cross-Episode Batch Size | 10 episodes |

The client requests JSON output, disables thinking and streaming, and asks Ollama not to truncate input.

Although the installed model metadata advertises a larger context capacity, this project requests only 16,384 tokens.

Generation is not seeded, so matching software versions do not guarantee identical results.

The `requirements.txt` file allows compatible Streamlit and ReportLab versions. No separate Ollama Python package is required; the application communicates through the local HTTP API.

References:
- [Qwen3 Model](https://ollama.com/library/qwen3:4b-instruct)
- [Ollama Quickstart](https://docs.ollama.com/quickstart)

---

## 4. Setup Instructions

### Prerequisites

- Python 3.10 or newer
- Ollama installed and running locally
- Git
- Internet connection for initial dependency and model downloads

### Step 1: Clone the Repository

Open PowerShell on Windows or Terminal on macOS/Linux.

```bash
git clone https://github.com/pulkitgulaticdciv23-sudo/kuku-promo-finder-PULKIT_23065119.git

cd kuku-promo-finder-PULKIT_23065119
```

Alternatively, use GitHub's Code → Download ZIP option and extract the project.

### Step 2: Install and Prepare Ollama

Install Ollama from:

https://ollama.com/download

Open a terminal and execute:

```bash
ollama --version

ollama pull qwen3:4b-instruct

ollama list

ollama show qwen3:4b-instruct
```

Verify that the exact model tag `qwen3:4b-instruct` appears in the model list.

If Ollama is not already running, start it in a separate terminal:

```bash
ollama serve
```

Check the local API on Windows:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/version
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

On macOS/Linux:

```bash
curl http://127.0.0.1:11434/api/version
curl http://127.0.0.1:11434/api/tags
```

An optional model check:

```bash
ollama run qwen3:4b-instruct "Reply with OK."
```

### Step 3: Create a Python Environment

**Windows — PowerShell**

```powershell
python --version

python -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 --version

python3 -m venv .venv

.venv/bin/python -m pip install -r requirements.txt
```

### Step 4: Start the Application

Ensure the local configuration file `config.stage4.local.json` is available.

**Windows**

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

**macOS / Linux**

```bash
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open the application:

http://localhost:8501

Keep Ollama and the Streamlit application running during processing.

Windows users can also use `START_STREAMLIT.bat` after completing the initial setup.

---

## 5. Input Format

The browser interface accepts a ZIP containing one or more UTF-8 `.txt` screenplay files.

Each episode should contain:

- Unique episode number
- Scene headings
- Timestamped dialogue and action blocks
- Start and end timestamps

Supported timestamp formats:

- `MM:SS`
- `MM:SS.fraction`

Each screenplay file must be no larger than 5 MiB.

PDF, DOCX, video, and ordinary untimed dialogue are not supported input formats.

### Example Screenplay Structure

```text
EPISODE NUMBER: Episode 1
EPISODE LOGLINE: A visitor brings an unexpected warning.
CONTINUITY NOTES: Fictional example.

--- SCRIPT START ---

SCENE 01
Scene heading: INT. ROOM - DAY
Scene start: 00:00.00
Scene end: 00:50.00

**ACTION:**
Start: (00:00.00) | End: (00:05.00)
A visitor rushes into the room holding a sealed envelope.

**ADA (Worried):**
Start: (00:05.00) | End: (00:15.00)
"You said nobody knew where we were."

**BOB (Quiet):**
Start: (00:15.00) | End: (00:30.00)
"I never told anyone. Who gave you that envelope?"

--- SCRIPT END ---
```

Preserve the `**LABEL:**` block syntax and the `Start: (...) | End: (...)` timing line.

Malformed screenplay blocks may be skipped by the parser. Always inspect parser warnings before trusting the results.

---

## 6. Using the Application

1. Open `http://localhost:8501`.
2. Select **Model Configuration → Local Ollama**.
3. Upload the screenplay ZIP.
4. Check the parsed episode count and expand any parser warnings.
5. Choose Run A and click **Find Promos**.
6. Inspect episodes processed, validated candidates, and ranked promos.
7. Open individual recommendations to review the hook, cliffhanger, explanation, required context, and score breakdown.
8. Verify suggested cuts against the actual video separately.
9. Under **Your Review**, choose Good, Bad, or Unsure and add review notes.
10. Download the review checklist or the generated CSV, JSON, evaluation, manual audit, or ranked report.

For a first-time installation, begin with a single episode.

A full-show run processes all parsed episodes and can take a considerable amount of time, particularly on CPU-only hardware.

Previously saved results can be reopened when the uploaded show matches the saved screenplay fingerprint.

---

## 7. Command-Line Batch Processing

The project also provides a command-line workflow for processing a screenplay ZIP or folder.

Create an `input_pack` folder and place an authorized screenplay ZIP named `show.zip` inside it.

### Windows

```powershell
New-Item -ItemType Directory -Force input_pack | Out-Null

.\.venv\Scripts\python.exe run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label A
```

### macOS / Linux

```bash
mkdir -p input_pack

.venv/bin/python run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label A
```

For a second run:

```powershell
.\.venv\Scripts\python.exe run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label B
```

Compare both runs:

```powershell
.\.venv\Scripts\python.exe compare_runs.py --a raw_outputs/run_A/promos.json --b raw_outputs/run_B/promos.json
```

The comparison generates `raw_outputs/stability_report.md`.

The comparison uses exact episode/start/end timestamps. Similar cuts with different boundaries are not counted as exact matches.

A/B labels change prompts and cache keys, so differences cannot automatically be attributed to model randomness alone.

---

## 8. Output Files

The application generates structured results and evaluation reports locally.

| Output | Purpose |
|---|---|
| `promos.json` | Ranked and unscored candidate records, status, and metrics |
| `promos.csv` | Tabular candidate output |
| `ranked_report.md` | Readable ranked results |
| `eval_report.md` | Processing coverage, validation checks, and errors |
| `manual_audit.csv` | Manual review checklist |
| `manual_audit.md` | Source-based audit report |
| `WHERE_AI_GOT_IT_WRONG.md` | Automatically detected failure cases |
| `checkpoint.json` | Processing checkpoint |
| `run_state.json` | Run configuration and execution state |

Typical local directories:

| Location | Purpose |
|---|---|
| `.runtime/uploads/` | Uploaded screenplay ZIPs |
| `.runtime/reviews/` | Saved user reviews |
| `.cache/llm/` | Cached model responses |
| `raw_outputs/run_<label>/` | Generated run outputs |
| `requests/` and `responses/` | Private model request/response records |

These directories may contain confidential screenplay content and should not be committed to the public repository.

The original assignment input pack and generated raw model outputs are not included in this public source repository.

---

## 9. Evaluation and Recorded Results

The project was evaluated on the provided 50-episode screenplay input pack.

### Screenplay Processing

| Metric | Recorded Result |
|---|---:|
| Episodes parsed | 50 |
| Scenes parsed | 100 |
| Timed events parsed | 3,727 |

### Recorded Model Runs

| Metric | Run A | Run B |
|---|---:|---:|
| Episodes processed | 50/50 | 50/50 |
| Ranked candidates | 81 | 71 |
| Unjudged candidates | 15 | 12 |
| Run status | Partial | Partial |

Run A additionally recorded:

- 115 proposals
- 100 candidates passing structural checks
- 85 candidates judged
- 15 candidates unjudged
- 81 final ranked candidates after overlap removal

### Manual Review

The top ten candidates from Run A were manually reviewed against their screenplay context.

| Verdict | Candidates |
|---|---:|
| Good | 4 |
| Unsure | 3 |
| Bad | 3 |

The review identified completed endings, slow openings, missing context, and similar scene themes.

This was a small, non-random source-text review, not an audience-performance benchmark.

Further evidence and limitations are documented in:

- `STAGE2_REAL_RUN.md`
- `SUBMISSION_STATUS.md`
- `WHERE_AI_GOT_IT_WRONG.md`

---

## 10. Scaling and Performance

Recorded Run A usage:

| Measure | Run A: 50 Episodes | Estimate: 100 Episodes |
|---|---:|---:|
| Input tokens | 534,047 | 1,068,094 |
| Output tokens | 58,606 | 117,212 |
| Model calls | 107 | 214 |
| Aggregate model processing | 87.8 minutes | 175.6 minutes |

The 100-episode figures are a simple linear projection based on Run A usage, assuming a similar workload, hardware, and processing outcome.

Model processing time is the sum of reported model-call durations, not end-to-end wall-clock time.

Local inference has no per-token API charge, although hardware and electricity costs still apply.

### Proposed Scaling Approach

For larger production workloads, I would:

1. Queue incoming shows for processing.
2. Distribute episode-level work across suitable workers.
3. Combine cross-episode candidates.
4. Run validation, overlap removal, and final ranking.
5. Present shortlisted results to human editors.

Parallel workers and distributed processing are proposed future improvements, not implemented features.

---

## 11. Limitations and Known Failure Cases

The project has several limitations:

1. **Same-model evaluation:** The same model proposes and judges candidates, so its assessments are not independent.
2. **Limited candidate search:** The candidate-generation process can miss potentially stronger promotional moments.
3. **Text-only evaluation:** The system cannot independently judge acting, music, visual impact, or final-video timing.
4. **Editorial boundary issues:** A structurally valid cut may still interrupt a continuing thought or resolve the dramatic tension too early.
5. **Diversity heuristics:** Episode blocks and speaker-label sets are approximate heuristics, not verified story arcs or resolved character identities.
6. **Partial execution:** Both recorded full-show runs processed all 50 episodes but retained unjudged candidates.

The manual review and failure reports document examples of incorrect cliffhanger interpretation, incorrect opening-action attribution, and editorially weak cut boundaries.

Human editorial review remains necessary before publishing suggested promos.

---

## 12. Troubleshooting

| Problem | What to do |
|---|---|
| Python not recognized | Install Python and enable PATH |
| Missing Streamlit module | Install dependencies using the same `.venv` Python |
| Ollama not recognized | Install Ollama and reopen the terminal |
| Cannot reach local Ollama | Start Ollama or run `ollama serve` |
| Model not found | Run `ollama pull qwen3:4b-instruct` |
| Port 8501 occupied | Use `--server.port 8502` |
| Parser warnings | Check UTF-8 encoding, timestamps, labels, and scene headings |
| Processing is slow | Start with one episode and keep the PC awake |
| Context limit exceeded | Inspect screenplay length and available memory |
| Partial run or no ranked promos | Inspect `eval_report.md`, `run_state.json`, and unscored JSON entries |
| Saved results missing | Re-upload the same show and check the run directory |

The Streamlit application is intended for local, single-user review. It binds to `127.0.0.1` and does not need to be exposed to the public internet.

---

## 13. Project Structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit application |
| `ui_helpers.py` | UI utilities and saved-result handling |
| `run_stage4.py` | Main processing pipeline |
| `screenplay.py` | Screenplay parsing |
| `validators.py` | Structural and source-evidence validation |
| `window_options.py` | Candidate window generation |
| `stage4_logic.py` | Candidate selection, scoring, and ranking |
| `stage4_prompts.py` | AI judging prompts |
| `llm_client.py` | Local Ollama and optional cloud model clients |
| `stage4_reports.py` | Evaluation and audit reports |
| `compare_runs.py` | A/B comparison |
| `STAGE2_REAL_RUN.md` | Earlier processing evidence |
| `SUBMISSION_STATUS.md` | Submission status and limitations |
| `WHERE_AI_GOT_IT_WRONG.md` | Documented failure cases |
| `run_stage3.py` | Earlier pipeline utility |
| `stage2_check.py` | Earlier validation utility |
| `requirements.txt` | Python dependencies |
| `config.stage4.local.json` | Local model configuration |

---

## 14. AI Tool Disclosure

ChatGPT was used to assist with code development and debugging. Claude was used to review the manual audit and solution document.

The model can be changed through configuration, and the recorded local runs use Qwen3 4B Instruct through Ollama.

The system's results, known failures, and manual review findings are documented separately.

---

**Kuku Promo Finder | AI Generalist Assignment | Problem 1**
