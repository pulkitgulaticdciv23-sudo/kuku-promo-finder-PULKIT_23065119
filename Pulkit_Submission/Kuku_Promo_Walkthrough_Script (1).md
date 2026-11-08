# Kuku: Find the Promos - Walkthrough Script

**Target:** Up to 3 minutes. Screen directions are not spoken; timings are approximate.

## Evidence for spoken figures (not spoken)

- Screenplay counts: `STAGE2_REAL_RUN.md`.
- Run A counts: `raw_outputs/run_A/eval_report.md` and `promos.json`.
- Model setup: `raw_outputs/run_A/run_state.json`.
- Audit and verdicts: `run_A_user_audit.csv`.
- Comparison and Jaccard: `raw_outputs/stability_report.md`; `3 / (10 + 10 - 3) = 17.6%`.
- Duration: `run_A/responses/*.success.body`, summing `total_duration`; the 100-episode projection doubles the 50-episode total.
- Errors: `WHERE_AI_GOT_IT_WRONG.md` and the source-backed Run A review.

## 00:00-00:25

**On screen:** Show the app title and local model settings.

I chose Find the Promos because it addresses Kuku's actual promo workflow and gives me timestamped outputs I can inspect. I chose local Ollama to run inference on my computer without depending on a paid API service. My recorded runs use Qwen3 4B Instruct; the model can be changed through configuration.

## 00:25-00:55

**On screen:** Show the input control and explain the pipeline. Keep confidential screenplay text hidden.

My code reads timed screenplay events, creates candidate time ranges, and checks duration, order, boundaries, and source-backed opening evidence. Qwen selects and explains promising cuts. The same model judges them in a separate pass. The final score combines 70% model judgments with 30% code-based signals. My code removes overlap and ranks the results. Streamlit lets me inspect outputs and record my review.

## 00:55-01:20

**On screen:** Open saved Run A and show its aggregate results. Make the partial status visible.

In Run A, I processed all fifty episodes. Out of one hundred fifteen proposals, one hundred passed structural checks. Eighty-five were judged, fifteen remained unjudged, and eighty-one were ranked after overlap filtering. The run is partial. Passing structural checks does not mean a promo will hold a viewer's attention.

## 01:20-01:50

**On screen:** First show one actual ranked promo with episode, timestamps, hook, cliffhanger, explanation and score breakdown. Then show the review section and checklist without displaying confidential script text.

I reviewed the top ten from the scripts. I marked four Good, three Unsure, and three Bad. My Good choices still have weaknesses, including slow openings and missing context. I also found cuts with resolved endings and similar scenes. This is my small editorial review, not a measure of audience response.

## 01:50-02:15

**On screen:** Show the evaluation report and a de-identified failure note.

One model explanation called an early question the cliffhanger, although the clip ended on a completed response. Another described a strike at the opening even though it happened before the cut. Earlier, ChatGPT also missed uppercase scene headings in a scan; case-insensitive matching corrected the count.

## 02:15-02:40

**On screen:** Show the comparison report; point to the overlap and Jaccard figures.

The two partial top-ten lists share three exact cuts: thirty percent of each list. Jaccard is seventeen point six percent because it divides the three shared cuts by all seventeen distinct cuts across both lists. Similar cuts with different boundaries do not count as exact matches, and the run labels also changed the prompts.

## 02:40-03:00

**On screen:** Show the local configuration and finish on limitations.

Run A's model calls total 87.8 minutes of reported processing, not wall time. Doubling that gives 175.6 minutes for a similar hundred-episode show. Parallel workers are proposed, not built. I used ChatGPT for exploration, code, prompts, and debugging.

## Recording note

Keep confidential screenplay text, raw prompts, private request/response logs, and any identifying script material off publicly accessible recordings. The solution uses partial real runs; do not describe the run counts as an accuracy score. The cited full-show runs are command-line runs. Keep the distinction between the recorded batch results and the app demonstration clear in the recording.
