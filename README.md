# Kuku Promo Finder

A local Python and Streamlit application that proposes **20–60 second promo cuts from timestamped microdrama screenplays**. It ranks candidates, explains the suggested hook and cliffhanger, and lets you inspect source text and save your own review.

**This project was run locally using the open-source Qwen3 model through Ollama.** The documented workflow needs no API key or paid model service. It analyzes screenplay text and exports suggested timestamps; it does not edit, render, or play video.

## 1. What you need

- **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/). On Windows, enable **Add Python to PATH** in the installer.
- **Ollama**, installed from the [official download page](https://ollama.com/download), running on the same PC as the application.
- **Git** from [git-scm.com](https://git-scm.com/downloads) to clone the repository, or use GitHub's **Code → Download ZIP** and extract it.
- An internet connection for the initial dependency and model downloads.
- Your own authorized screenplay `.txt` files in the format shown below. **The confidential assignment input pack and its generated results are not included in this repository.**

The model download is about **2.5 GB**, in addition to Python dependencies and saved outputs. Runtime memory use is higher than the model file size and increases with context length. A GPU is optional; CPU inference can be slow. No measured minimum RAM or GPU requirement is established for this project. Start with one episode before processing a full show.

After dependencies and the model are downloaded, the Local Ollama workflow can run without a cloud model connection. Keep Ollama and the application running while processing.

## 2. Model and verified local environment

These values were inspected on the author's Windows PC on **6 November 2026**. They describe the currently verified environment; they are not a record of the exact software versions used for every earlier run.

| Component | Verified value |
|---|---|
| Python | 3.13.5 |
| Ollama runtime | 0.40.15 |
| Model family | Qwen3 |
| Exact Ollama model tag used by this project | `qwen3:4b-instruct` |
| Parameters | Approximately 4 billion |
| Quantization / file format | Q4_K_M / GGUF |
| Download size | 2,497,293,803 bytes, approximately 2.5 GB |
| Model ID | `0edcdef34593` |
| Model license | Apache License 2.0 |
| Streamlit | 1.67.0 |
| ReportLab | 4.5.1 |
| Inference location | Local computer, through `http://127.0.0.1:11434/api/chat` |

Full installed model digest:

```text
0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0
```

Ollama is the runtime; **Qwen3 is the model**. The tag, quantization, and digest identify the model more precisely than the runtime version alone. The model's license does not automatically license this repository's code or any screenplay inputs.

The default application configuration is [`config.stage4.local.json`](config.stage4.local.json):

| Setting | Value |
|---|---|
| Provider | `ollama_local` |
| Model | `qwen3:4b-instruct` |
| Context requested by this project | 16,384 tokens |
| Maximum generated tokens per request | 3,072 |
| Temperature | 0.7 |
| Request timeout | 1,800 seconds |
| Request rate limit | 60 requests per minute, including judging |
| Candidates per episode | Up to 2 |
| Judge batch size | 2 |
| Cross-episode batch size | 10 episodes |

The client requests JSON output, disables thinking and streaming, and asks Ollama not to truncate input. Although the installed model metadata advertises a 262,144-token context capacity, **this project requests only 16,384 tokens**. A larger context needs more memory. Generation is not seeded, so matching versions does not guarantee identical results.

`requirements.txt` allows `streamlit>=1.40,<2` and `reportlab>=4,<5`; installing it may resolve versions different from the verified environment above. ReportLab supports PDF-related project utilities; it is not the model runtime. There is no separate Ollama Python package required: the code calls the local HTTP API.

References: [official model listing](https://ollama.com/library/qwen3:4b-instruct), [Ollama quickstart](https://docs.ollama.com/quickstart).

## 3. Download the project

Open PowerShell on Windows, or Terminal on macOS/Linux:

```text
git clone https://github.com/Lakshyasaxena5/LAKSHYA_23118042_KUKU_SUBMISSION.git
cd LAKSHYA_23118042_KUKU_SUBMISSION
```

If you downloaded a ZIP instead, extract it and open a terminal inside the extracted folder containing `app.py` and `requirements.txt`. Run all subsequent project commands from that folder.

## 4. Prepare Ollama and download the model

Install and open Ollama, then open a new terminal and run:

```text
ollama --version
ollama pull qwen3:4b-instruct
ollama list
ollama show qwen3:4b-instruct
```

`ollama list` should contain the exact tag `qwen3:4b-instruct`. Compare its ID with `0edcdef34593` when checking the model used here; tags can change over time.

If the service is not already running, start it in a separate terminal:

```text
ollama serve
```

Keep that terminal open. If it reports that port 11434 is already in use, Ollama may already be running; do not start a second server.

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

An optional inference check is `ollama run qwen3:4b-instruct "Reply with OK."`. The first model load can take time. No account or API key is required for this local model workflow.

## 5. Install dependencies and start the app

### Windows — PowerShell

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Use `python` as shown. If your installation exposes only the Windows launcher, substitute `py -3` for `python` in the first two commands. You do not need to activate the virtual environment or change PowerShell's execution policy.

### macOS / Linux — Terminal

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

If your Linux distribution says `venv` is unavailable, install its Python venv package using your distribution's package manager, then retry.

Open **[http://localhost:8501](http://localhost:8501)** in your browser if it does not open automatically. Keep the terminal open. Press **Ctrl+C** in that terminal to stop the app.

For later launches, repeat only the final Streamlit command with Ollama running. Windows users can also double-click `START_STREAMLIT.bat` after setup; that launcher checks for the `py` launcher, so use the PowerShell command if `py` is unavailable.

## 6. Prepare your screenplay ZIP

The browser accepts a **ZIP containing one or more UTF-8 `.txt` screenplay files**, typically one file per episode, such as `episode-001.txt`. The command-line pipeline also accepts a folder of these files. PDF, DOCX, video, and ordinary untimed dialogue are not supported input formats.

Use unique episode numbers. Each episode has its own timeline, which can restart at zero. Use `MM:SS` or `MM:SS.fraction` timestamps, with seconds from 00 to 59. Each file must be no larger than 5 MiB.

This fictional format example can be saved as `episode-001.txt` and zipped. It illustrates parsing, not a guarantee that the model will select a promo:

```text
EPISODE NUMBER: Episode 1
EPISODE LOGLINE: A visitor brings an unexpected warning.
CONTINUITY NOTES: Fictional example for setup.

--- SCRIPT START ---

SCENE 01
Scene heading: INT. ROOM - DAY
Scene start: 00:00.00
Scene end: 00:50.00

Actions and dialogue:

**ACTION:**
Start: (00:00.00) | End: (00:05.00)
A visitor rushes into the room holding a sealed envelope.

**ADA (Worried):**
Start: (00:05.00) | End: (00:15.00)
"You said nobody knew where we were."

**BOB (Quiet):**
Start: (00:15.00) | End: (00:30.00)
"I never told anyone. Who gave you that envelope?"

**ACTION:**
Start: (00:30.00) | End: (00:40.00)
Ada breaks the seal and stares at the photograph inside.

**ADA (Shocked):**
Start: (00:40.00) | End: (00:50.00)
"Then why is your name on the back?"

--- SCRIPT END ---
```

Preserve the `**LABEL:**` block syntax and `Start: (...) | End: (...)` timing line. Scene field names are case-insensitive. The parser reports malformed input and may skip invalid blocks; inspect every warning before trusting the results.

## 7. Use the app

1. Open `http://localhost:8501` and leave **Model configuration → Local Ollama — no API key** selected.
2. Upload the screenplay ZIP. Alternatively, place your authorized ZIP at `input_pack/show.zip`; the app then offers **Use the show on this computer**.
3. Check the parsed episode count and expand any **Parser warnings**. For your first run, use a ZIP containing just one episode.
4. Choose **Run A** and click **Find promos**. This processes all parsed episodes; there is no episode-count selector. Keep the browser, application, and Ollama running, and keep the PC awake. Full-show CPU runs can take a long time.
5. Inspect **Episodes processed**, **Validated candidates**, and **Ranked promos**. A **partial** run can still have results; read its evaluation report to see missing episodes or judge scores.
6. Under **Choose a promo to inspect**, review the hook, cliffhanger, explanation, required context, **Score breakdown**, and **View the screenplay for this promo**. Verify the cut against the actual video separately.
7. In **Your review**, choose **Good**, **Bad**, or **Unsure**, enter notes, and click **Save my review**. Decisions are saved locally, separately from model outputs. Use **Download my review checklist** to export them.
8. Under **Download results**, export CSV, JSON, evaluation, manual audit, blank audit template, or ranked report.
9. To reopen a previous result, upload the same show, choose an entry under **Saved results for this show**, and click **Open saved results**. Saved results are offered only when the parsed-show fingerprint matches. Earlier CLI `run_A`/`run_B` folders can also be reviewed if they are under `raw_outputs/` and match the uploaded show.
10. Optionally select **Run B** and run again. A/B labels change prompts and cache keys. When both matching UI runs are saved, the app displays exact top-10 overlap; this is a stability check, not proof of editorial quality or independent model judgments.

UI run directories use generated labels such as `run_UI_..._A`; command-line labels `A` and `B` produce `run_A` and `run_B`.

## 8. Where files are saved

| Location / file | Purpose |
|---|---|
| `.runtime/uploads/` | Local copies of uploaded ZIPs |
| `.runtime/reviews/` | Saved user verdicts and notes |
| `.cache/llm/` | Cached model responses |
| `raw_outputs/run_<label>/promos.json` | Ranked and unscored candidates, status, and metrics |
| `promos.csv`, `ranked_report.md` inside that run folder | Tabular results and readable ranking |
| `eval_report.md` | Processing coverage, structural checks, errors, and available token counts |
| `manual_audit.md`, `manual_audit.csv` | Source-based review material and blank verdict fields |
| `WHERE_AI_GOT_IT_WRONG.md` inside that run folder | Automatically detected failures |
| `checkpoint.json`, `run_state.json` | Processing checkpoint and exported run state |
| `requests/`, `responses/` inside that run folder | Private model request/response records |

These runtime directories are ignored by Git. Downloads and local logs can contain screenplay text; keep them private. Local mode sends model requests only to the loopback Ollama service. The app also exposes optional cloud configurations; selecting one changes the processing destination and requires the corresponding environment API key. They are not needed for the documented local setup.

## 9. Optional command-line batch workflow

Create `input_pack`, put your authorized ZIP there as `show.zip`, then run:

### Windows

```powershell
New-Item -ItemType Directory -Force input_pack | Out-Null
# Place show.zip in input_pack before running the next command.
.\.venv\Scripts\python.exe run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label A
```

### macOS / Linux

```bash
mkdir -p input_pack
# Place show.zip in input_pack before running the next command.
.venv/bin/python run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label A
```

To use a folder, replace `input_pack/show.zip` with the folder path containing your `.txt` files. Quote paths containing spaces. Check `raw_outputs/run_A/eval_report.md` and the `status` in `promos.json` when the command ends.

For a second run and comparison on Windows:

```powershell
.\.venv\Scripts\python.exe run_stage4.py --input input_pack/show.zip --config config.stage4.local.json --run-label B
.\.venv\Scripts\python.exe compare_runs.py --a raw_outputs/run_A/promos.json --b raw_outputs/run_B/promos.json
```

On macOS/Linux, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`. Comparison writes `raw_outputs/stability_report.md` and updates both evaluation reports. It compares exact episode/start/end cuts; similar cuts with different boundaries do not count as matches.

After an interruption, rerun the same input, configuration, and label to reuse cached calls. Successful HTTP responses containing invalid JSON are also cached, so repeating a label does not necessarily repair them. Use a new label for fresh requests after inspecting the failure. The pipeline stops after three consecutive episode-provider failures and records a partial result.

## 10. Troubleshooting

| Problem | What to do |
|---|---|
| `python` is not recognized or opens the Microsoft Store | Install Python from python.org, enable PATH, and reopen the terminal. If available, use `py -3` on Windows. |
| `py -3` says no installed Python was found | Try `python --version`; use the `python` setup commands above if that works. |
| `No module named streamlit` | Run the requirements installation with the same `.venv` Python used to start the app. |
| `ollama` is not recognized | Install Ollama and reopen your terminal. |
| Could not reach local Ollama | Open Ollama or run `ollama serve`; verify the API checks in step 4. |
| Model not found | Run `ollama pull qwen3:4b-instruct`; verify that the exact tag appears in `ollama list`. |
| Port 8501 is occupied | Open the already-running app, or launch with `--server.port 8502` and visit `http://localhost:8502`. |
| No readable episodes or parser warnings | Check UTF-8 encoding, `.txt` files, unique episode numbers, scene headings, block syntax, and timestamps. |
| Processing is slow or times out | Try one episode, close memory-heavy apps, and keep the PC awake. The default timeout is 30 minutes per request; a full show involves many requests. |
| Context limit exceeded / out of memory | Inspect input length. Increasing `context_tokens` in `config.stage4.local.json` needs more memory; a larger advertised model context does not guarantee your PC can handle it. |
| Partial run or no ranked promos | Read `eval_report.md`, `run_state.json`, and the JSON `unscored` entries. Structurally valid candidates without accepted judge scores are not ranked with invented scores. |
| Saved result is missing | Use the same show and model configuration, and ensure its run folder is under the configured `raw_outputs/` directory. |

The Streamlit app is intended for local, single-user review. The startup commands bind it to `127.0.0.1`; there is no need to expose it to the internet.

## 11. Verification and project structure

Run the test suite from the project folder:

```powershell
# Windows
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

```bash
# macOS / Linux
.venv/bin/python -m unittest discover -s tests -q
```

The verified suite contains **74 tests**. Tests use fictional scripts, mocked model/UI responses, and a local test HTTP server; they do not download a model or establish editorial quality. A simulated HTTP 429 retry message is expected in the tests.

| File | Role |
|---|---|
| `app.py`, `ui_helpers.py` | Streamlit interface, uploads, saved results, and user reviews |
| `run_stage4.py` | Main batch pipeline used by the app and CLI |
| `screenplay.py`, `validators.py` | Parse input and validate cuts/source evidence |
| `window_options.py`, `stage4_logic.py`, `stage4_prompts.py` | Candidate options, selection, and judging prompts |
| `llm_client.py` | Local Ollama and optional cloud HTTP clients, caching, and retries |
| `stage4_reports.py`, `compare_runs.py` | Reports and A/B comparison |
| `config.stage4.local.json` | Local settings used by the documented workflow |
| `tests/` | Automated tests with fictional fixtures |
| `run_stage3.py`, `stage2_check.py` | Earlier pipeline/check utilities; not required for the main app workflow |
| `submission/Kuku_Promo_Solution_Final.pdf` | Two-page solution note |
| `submission/Kuku_Promo_Walkthrough_Script.md` | Submission walkthrough script |

## 12. How results are produced and their limits

The parser reads timed source events. Code builds a limited menu of possible 20–60 second cuts; the local model selects candidates and explains them. The pipeline also offers bounded two-episode combinations, validates chronology and evidence, asks the same model to judge accepted candidates, removes overlapping cuts, and applies diversity penalties.

Default scoring combines **70% model judgments and 30% code features**. Weights are editable in `scoring_weights` and must sum to 1:

| Dimension | Weight |
|---|---:|
| Hook strength | 0.17 |
| Cliffhanger strength | 0.20 |
| Standalone clarity | 0.15 |
| Emotional intensity | 0.07 |
| Genre fit | 0.05 |
| Spoiler avoidance | 0.06 |
| Speech begins in the first three seconds | 0.08 |
| Dialogue density | 0.06 |
| Length near 35–55 seconds | 0.10 |
| Earlier episode | 0.06 |

Judge scores are mapped from 1–10 to 0–1; spoiler risk is reversed for spoiler avoidance. Overlaps use the configured intersection-over-union threshold of 0.6. Greedy re-ranking discourages repeated episodes, five-episode blocks, and literal speaker-label sets. These are heuristics; five-episode blocks are not verified story arcs and speaker aliases are not resolved.

The proposer and judge use the **same model**, so their assessments are not independent. The shortlist can miss better cuts. Source checks do not prove that unquoted explanations are factually correct, that every sentence ends cleanly, or that the video matches the screenplay. Review the actual video before using a suggested promo.

Recorded project evidence: two local runs processed 50/50 episodes. Run A produced 81 ranked and 15 unjudged candidates; Run B produced 71 ranked and 12 unjudged candidates. **Both were partial runs.** One manual review of Run A's top ten recorded 4 Good, 3 Bad, and 3 Unsure verdicts. These results are not an audience benchmark, and generalization to a different show's format remains untested. See [`SUBMISSION_STATUS.md`](SUBMISSION_STATUS.md) for the evidence and limitations.
