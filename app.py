"""Local Streamlit interface for the Kuku promo finder."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from compare_runs import compare
from llm_client import ProviderError, load_config
from run_stage4 import execute, show_fingerprint
from screenplay import load_show
from ui_helpers import (load_reviews, review_csv, review_path, save_review,
                        script_lines, store_upload, table_rows, ui_run_label)


ROOT = Path(__file__).resolve().parent
CONFIGS = {
    "Local Ollama — no API key": "config.stage4.local.json",
    "Gemini — environment key": "config.stage4.gemini.example.json",
    "OpenAI-compatible — environment key": "config.stage4.openai.example.json",
}

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
st.title("Promo Finder")
st.caption("Find and review possible 20–60 second promos from screenplay timestamps.")
st.info("This is a local review tool. Model scores are suggestions; check the script and video before publishing.")

with st.sidebar:
    st.header("Settings")
    selected = st.selectbox("Model configuration", list(CONFIGS))
    variant = st.radio("Run", ["A", "B"], horizontal=True,
                       help="A and B request separate outputs for the stability comparison.")
    st.caption("Edit the selected configuration file to change the model, endpoint, request rate, or scoring weights.")
    st.caption("Uploaded scripts and generated outputs are stored on this machine in ignored folders.")

local_show = ROOT / "input_pack" / "show.zip"
upload_data = None
if local_show.is_file():
    source_choice = st.radio("Screenplay input", ["Use the show on this computer", "Upload another ZIP"],
                             horizontal=True)
    if source_choice == "Use the show on this computer":
        try:
            upload_data = local_show.read_bytes()
        except OSError as exc:
            st.error(f"Could not read the local screenplay ZIP: {exc}")
            st.stop()
if upload_data is None:
    upload = st.file_uploader("Upload a ZIP of screenplay .txt files", type=["zip"])
    if upload is None:
        st.write("Choose a screenplay ZIP, then select **Find promos**. No key is needed for local Ollama.")
        st.stop()
    upload_data = upload.getvalue()

try:
    source_path, upload_hash = store_upload(upload_data, ROOT / ".runtime" / "uploads")
    episodes, parser_warnings = load_show(source_path)
    if not episodes:
        raise ValueError("No episodes could be parsed. Check that the ZIP contains screenplay .txt files.")
    config = load_config(ROOT / CONFIGS[selected])
    config["cache_dir"] = str(ROOT / config["cache_dir"])
    config["raw_outputs_dir"] = str(ROOT / config["raw_outputs_dir"])
    source_fingerprint = show_fingerprint(episodes)
    label = ui_run_label(upload_hash, config, variant)
    output_dir = Path(config["raw_outputs_dir"]) / f"run_{label}"
except (ValueError, OSError, ProviderError) as exc:
    st.error(str(exc))
    st.stop()

st.caption(f"Parsed {len(episodes)} episode(s). Run {variant} will use the selected model configuration.")
if parser_warnings:
    with st.expander(f"Parser warnings ({len(parser_warnings)})"):
        for warning in parser_warnings:
            st.text(warning)

saved = {}
for path in sorted(Path(config["raw_outputs_dir"]).glob("run_*/promos.json")):
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("show_fingerprint") == source_fingerprint:
            saved[f"{path.parent.name} ({existing.get('status', 'unknown')})"] = path.parent
    except (OSError, ValueError, AttributeError):
        continue
saved_choice = st.selectbox("Saved results for this show", list(saved)) if saved else None
if saved:
    st.caption("Open saved results to review an earlier run. Find promos starts a new model run and can take a long time.")
left, right = st.columns([1, 2])
run_clicked = left.button("Find promos", type="secondary" if saved else "primary")
load_clicked = right.button("Open saved results", type="primary" if saved else "secondary", disabled=not saved)
if run_clicked:
    progress_bar = st.progress(0, text="Starting the model")
    status_text = st.empty()
    def update_progress(info):
        phase, done, total = info["phase"], info["done"], max(1, info["total"])
        start, weight = {"episodes": (0.0, 0.60), "cross": (0.60, 0.10), "judge": (0.70, 0.29), "done": (1.0, 0.0)}[phase]
        progress_bar.progress(min(1.0, start + weight * done / total), text=info["message"])
        status_text.caption(f"{phase.capitalize()}: {done}/{total}")
    try:
        with st.spinner("Analyzing the show. This can take a long time on a CPU; keep the app open."):
            _, output_dir = execute(str(source_path), config, label, progress=update_progress)
        st.session_state["active_result"] = str(output_dir)
    except (ValueError, OSError, ProviderError) as exc:
        st.error(f"Run could not start: {exc}")

if load_clicked:
    st.session_state["active_result"] = str(saved[saved_choice])

if not st.session_state.get("active_result"):
    st.stop()

try:
    output_dir = Path(st.session_state["active_result"])
    results = json.loads((output_dir / "promos.json").read_text(encoding="utf-8"))
    if results.get("show_fingerprint") != source_fingerprint:
        st.info("Select a saved run for this show, or run the uploaded show again.")
        st.stop()
except (OSError, ValueError) as exc:
    st.error(f"Could not read results: {exc}")
    st.stop()

metrics = results.get("metrics", {})
st.subheader("Results")
if results.get("status") != "completed":
    st.warning("This run is partial. Check the evaluation report for missing episodes or judge results.")
else:
    st.success("Processing completed. The promos still need editorial review.")
cols = st.columns(3)
cols[0].metric("Episodes processed", f"{metrics.get('episodes_processed', 0)}/{metrics.get('episodes_total', len(episodes))}")
cols[1].metric("Validated candidates", metrics.get("validated", 0))
cols[2].metric("Ranked promos", len(results.get("ranked", [])))
active_label = results.get("run_label", label)
if active_label in ("A", "B"):
    other_label = "B" if active_label == "A" else "A"
else:
    other_variant = "B" if active_label.endswith("_A") else "A"
    other_label = ui_run_label(upload_hash, config, other_variant)
other_results = Path(config["raw_outputs_dir"]) / f"run_{other_label}" / "promos.json"
if other_results.is_file():
    try:
        comparison = compare(results, json.loads(other_results.read_text(encoding="utf-8")))
        overlap = comparison["jaccard_percent"]
        st.caption(f"Run A/B exact top-10 overlap: {comparison['exact_matches']} shared; "
                   f"Jaccard {overlap if overlap is not None else 'N/A'}%. "
                   "Short or partial runs limit interpretation.")
    except (OSError, ValueError, KeyError, TypeError):
        st.caption("Could not compare the saved A and B results; check that they use the same settings.")
ranked = results.get("ranked", [])
if not ranked:
    st.warning("There are no ranked promos. Unscored candidates and errors are in the JSON and evaluation report.")
else:
    st.dataframe(table_rows(ranked), hide_index=True, use_container_width=True)
    st.subheader("Review promo details")
    options = [f"#{c['rank']} — {c['candidate_id']} — {c['duration_seconds']:.1f}s" for c in ranked]
    choice = st.selectbox("Choose a promo to inspect", options)
    candidate = ranked[options.index(choice)]
    st.write("**Hook:**", candidate["hook"]["quote"])
    st.write("**Model's cliffhanger:**", candidate["cliffhanger"])
    st.write("**Model's reason:**", candidate["why_it_works"])
    st.write("**Context the model says is needed:**", candidate["context_needed"])
    with st.expander("Score breakdown"):
        st.json(candidate["score_breakdown"], expanded=False)
    with st.expander("View the screenplay for this promo"):
        st.caption("Source blocks that cross a cut are marked; the full block may extend outside the clip.")
        st.code("\n".join(script_lines(candidate, episodes)), language=None)

    st.subheader("Your review")
    st.caption("Your decisions are saved separately; the original model outputs are not changed.")
    review_file = review_path(ROOT / ".runtime" / "reviews", source_fingerprint, str(active_label))
    try:
        reviews = load_reviews(review_file)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    current = reviews.get(candidate["candidate_id"], {})
    verdicts = ["Not reviewed", "Good", "Bad", "Unsure"]
    previous = current.get("verdict", "Not reviewed")
    if previous not in verdicts:
        previous = "Not reviewed"
    form_key = review_file.stem + "_" + candidate["candidate_id"]
    with st.form(key=form_key):
        verdict = st.radio("Your verdict", verdicts, index=verdicts.index(previous), horizontal=True)
        notes = st.text_area("Your notes", value=current.get("notes", ""),
                             placeholder="For example: Is the hook strong? Does the ending leave a real question?")
        submitted = st.form_submit_button("Save my review")
    if submitted:
        try:
            reviews = save_review(review_file, candidate["candidate_id"], verdict, notes)
            st.success("Your review has been saved on this computer.")
        except (OSError, ValueError) as exc:
            st.error(f"Could not save your review: {exc}")
    top = ranked[:10]
    reviewed = sum(reviews.get(c["candidate_id"], {}).get("verdict") in ("Good", "Bad", "Unsure") for c in top)
    st.caption(f"Your top-10 review: {reviewed} of {len(top)} promos rated.")
    st.download_button("Download my review checklist", data=review_csv(ranked, reviews),
                       file_name=f"run_{active_label}_user_audit.csv", mime="text/csv")

st.subheader("Download results")
for filename, label_text, mime in (
    ("promos.csv", "Download CSV", "text/csv"),
    ("promos.json", "Download JSON", "application/json"),
    ("eval_report.md", "Download evaluation", "text/markdown"),
    ("manual_audit.md", "Download manual audit", "text/markdown"),
    ("manual_audit.csv", "Download blank audit template", "text/csv"),
    ("ranked_report.md", "Download ranked report", "text/markdown"),
):
    path = output_dir / filename
    if path.is_file():
        st.download_button(label_text, data=path.read_bytes(), file_name=filename, mime=mime, key=f"download_{filename}")

if results.get("unscored"):
    st.caption(f"{len(results['unscored'])} structurally valid candidates could not be scored and are listed in the JSON.")
