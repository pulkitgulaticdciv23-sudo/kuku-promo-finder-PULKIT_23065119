"""Streamlit interface for the Kuku promo finder (local or hosted)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from compare_runs import compare
from llm_client import ProviderError, load_config
from run_stage4 import execute, show_fingerprint
from screenplay import load_show, show_counts
from stage4_logic import source_events
from ui_helpers import (load_reviews, review_csv, review_path, save_review,
                        script_lines, store_upload, table_rows, ui_run_label)


ROOT = Path(__file__).resolve().parent
# Use the existing Gemini configuration for both the hosted demo and local run.
# No new provider, model, or API key is introduced here.
CONFIG_FILE = "config.stage4.gemini.example.json"


def _screenplay_diagnostics(episodes: dict) -> dict:
    events = [event for episode in episodes.values() for event in episode.get("events", [])]
    return {
        "episodes": len(episodes),
        "scenes": sum(len(episode.get("scenes", [])) for episode in episodes.values()),
        "dialogue": sum(event.get("type") == "dialogue" for event in events),
        "actions": sum(event.get("type") == "action" for event in events),
        "camera_blocks": sum(event.get("type") == "camera" for event in events),
        "cut_blocks": sum(event.get("type") == "transition" for event in events),
        "inline_cut_cues": sum(bool(event.get("cut_cues")) for event in events),
        "sound_fx": sum(event.get("type") == "sound_fx" for event in events),
    }


def _promo_source_rows(candidate: dict, episodes: dict) -> list[dict]:
    rows = []
    for segment_index, segment in enumerate(candidate.get("segments", []), start=1):
        episode = episodes[segment["episode"]]
        scene_by_id = {scene.get("scene_id"): scene for scene in episode.get("scenes", [])}
        for event in source_events(episode, segment, include_overlaps=True):
            scene = scene_by_id.get(event.get("scene_id"), {})
            kind = event.get("type", "unknown")
            if kind == "dialogue":
                source_text = f"{event.get('speaker') or 'Character'}: {event.get('text', '')}"
            else:
                source_text = event.get("text", "")
            rows.append({
                "Segment": segment_index,
                "Episode": segment["episode"],
                "Scene": scene.get("heading") or event.get("scene_id"),
                "Time": f"{event.get('start', '')}–{event.get('end', '')}",
                "Cue type": kind.replace("_", " ").title(),
                "Source label": event.get("original_label", ""),
                "Cut cue": ", ".join(event.get("cut_cues", [])),
                "Screenplay evidence": source_text,
            })
    return rows

st.set_page_config(page_title="Kuku Promo Finder", page_icon="🎬", layout="wide")

# On Streamlit Community Cloud, expose the SAME existing Gemini key through
# App Settings -> Secrets. Locally, an already-set GEMINI_API_KEY environment
# variable continues to work. The key is never written into config or source.
try:
    _existing_gemini_key = st.secrets.get("GEMINI_API_KEY")
except Exception:
    _existing_gemini_key = None
if _existing_gemini_key:
    os.environ.setdefault("GEMINI_API_KEY", str(_existing_gemini_key))

# Visual layer only: the existing parsing, model, ranking, review and export logic
# below remains unchanged.
st.markdown(
    """
    <style>
    :root {
        color-scheme: dark;
        --bg: #08080c;
        --panel: #111117;
        --panel-2: #17151d;
        --line: #302532;
        --pink: #f0a6c8;
        --pink-strong: #ff8fc3;
        --text: #f5f1f4;
        --muted: #aaa1aa;
    }
    .stApp {
        background:
            radial-gradient(ellipse at 12% 0%, rgba(190, 75, 130, .13), transparent 34%),
            radial-gradient(ellipse at 92% 8%, rgba(125, 78, 120, .10), transparent 30%),
            var(--bg);
        color: var(--text);
    }
    [data-testid="stHeader"] { background: rgba(8, 8, 12, .82); }
    [data-testid="stSidebar"] {
        background: #0d0c12;
        border-right: 1px solid var(--line);
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] label {
        color: var(--pink);
    }
    [data-testid="stAppViewContainer"] .main .block-container {
        max-width: 1240px;
        padding-top: 2.2rem;
        padding-bottom: 3.5rem;
    }
    h1, h2, h3 {
        color: var(--pink) !important;
        letter-spacing: -0.035em;
    }
    p, label, li, [data-testid="stCaptionContainer"] {
        color: var(--text);
    }
    [data-testid="stCaptionContainer"] {
        color: var(--muted) !important;
    }
    .hero-shell {
        position: relative;
        overflow: hidden;
        padding: 1.65rem 1.8rem 1.35rem;
        margin: 0 0 1.3rem 0;
        border: 1px solid rgba(240, 166, 200, .25);
        border-radius: 22px;
        background:
            radial-gradient(ellipse at 88% 0%, rgba(240, 166, 200, .14), transparent 42%),
            linear-gradient(135deg, #17121b 0%, #100f15 58%, #171019 100%);
        box-shadow: 0 18px 55px rgba(0, 0, 0, .24);
    }
    .hero-brand {
        display: flex;
        align-items: center;
        gap: .6rem;
        color: #c9bac5;
        font-size: .72rem;
        font-weight: 700;
        letter-spacing: .16em;
        text-transform: uppercase;
    }
    .hero-brand-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 27px;
        height: 27px;
        border: 1px solid rgba(255, 143, 195, .55);
        border-radius: 9px;
        color: var(--pink-strong);
        font-size: .9rem;
        letter-spacing: 0;
    }
    .hero-eyebrow {
        margin: 1.45rem 0 .45rem;
        color: var(--pink-strong);
        font-size: .72rem;
        font-weight: 750;
        letter-spacing: .18em;
        text-transform: uppercase;
    }
    .hero-title {
        max-width: 850px;
        margin: 0;
        color: #fff7fb !important;
        font-size: clamp(2rem, 4.2vw, 3.45rem);
        font-weight: 780;
        line-height: 1.06;
        letter-spacing: -.055em;
    }
    .hero-title span {
        color: var(--pink) !important;
        font-style: italic;
    }
    .hero-subtitle {
        max-width: 720px;
        margin: .85rem 0 1.35rem;
        color: #c9bdc7;
        font-size: 1rem;
        line-height: 1.6;
    }
    .hero-pills {
        display: flex;
        flex-wrap: wrap;
        gap: .55rem;
    }
    .hero-pill {
        padding: .42rem .7rem;
        border: 1px solid rgba(240, 166, 200, .24);
        border-radius: 999px;
        color: #e8cfdd;
        background: rgba(255, 255, 255, .035);
        font-size: .67rem;
        font-weight: 700;
        letter-spacing: .08em;
    }
    .section-kicker {
        margin: .8rem 0 .45rem;
        color: var(--pink-strong);
        font-size: .69rem;
        font-weight: 800;
        letter-spacing: .16em;
        text-transform: uppercase;
    }
    [data-testid="stMetric"] {
        padding: 1rem 1.05rem;
        border: 1px solid var(--line);
        border-radius: 15px;
        background: linear-gradient(145deg, #17141c, #101015);
    }
    [data-testid="stMetricLabel"] {
        color: #c9b9c5 !important;
    }
    [data-testid="stMetricValue"] {
        color: var(--pink) !important;
    }
    div.stButton > button,
    div.stDownloadButton > button,
    div[data-testid="stFormSubmitButton"] > button {
        border: 1px solid rgba(240, 166, 200, .48);
        border-radius: 11px;
        background: linear-gradient(135deg, #f0a6c8, #d987b3);
        color: #21121c;
        font-weight: 750;
        transition: transform .15s ease, filter .15s ease;
    }
    div.stButton > button:hover,
    div.stDownloadButton > button:hover,
    div[data-testid="stFormSubmitButton"] > button:hover {
        border-color: var(--pink-strong);
        filter: brightness(1.07);
        transform: translateY(-1px);
        color: #160e14;
    }
    div[data-testid="stFileUploader"] section {
        border: 1px dashed rgba(240, 166, 200, .42);
        border-radius: 16px;
        background: rgba(240, 166, 200, .035);
    }
    div[data-testid="stFileUploader"] section:hover {
        border-color: var(--pink-strong);
    }
    [data-testid="stAlert"] {
        border: 1px solid rgba(240, 166, 200, .25);
        border-radius: 13px;
        background: rgba(240, 166, 200, .055);
    }
    [data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: 13px;
        background: rgba(255, 255, 255, .018);
    }
    [data-testid="stDataFrame"] {
        border: 1px solid var(--line);
        border-radius: 13px;
        overflow: hidden;
    }
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div,
    div[data-baseweb="textarea"] > div {
        background: #17141c;
        border-color: #493342;
    }
    hr {
        border-color: var(--line);
    }
    @media (max-width: 700px) {
        .hero-shell { padding: 1.25rem; border-radius: 16px; }
        .hero-subtitle { font-size: .9rem; }
    }
    </style>

    <div class="hero-shell">
      <div class="hero-brand">
        <span class="hero-brand-mark">K</span>
        <span>Kuku / Promo Finder</span>
      </div>
      <div class="hero-eyebrow">AI-assisted editorial workspace</div>
      <h1 class="hero-title">Turn storylines into <span>scroll-stopping promos.</span></h1>
      <p class="hero-subtitle">
        Find the moment. Control the reveal. Cut before the payoff.
        Read dramatic beats, character stakes and unresolved endings from the
        screenplay, then keep the final editorial decision in human hands.
      </p>
      <div class="hero-pills">
        <span class="hero-pill">20–60 SECOND PROMOS</span>
        <span class="hero-pill">DIRECTOR-STYLE STORY ANALYSIS</span>
        <span class="hero-pill">HUMAN EDITORIAL REVIEW</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.info(
    "This is a screenplay-first editorial tool. It analyses hooks, escalation, "
    "character stakes, near-misses, reveal control and the cut-out point using "
    "scripted events and camera directions. It does NOT inspect finished footage: "
    "actual aspect ratio, pixel resolution, framing, transitions and camera motion "
    "must be checked against the video. The default Gemini option uses the existing "
    "API key configured in app Secrets. Uploaded screenplay text is sent to Gemini; "
    "upload only material you are authorized to process."
)
st.markdown('<div class="section-kicker">01 / Add your screenplay</div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("Settings")
    variant = st.radio("Run", ["A", "B"], horizontal=True,
                       help="A and B request separate outputs for the stability comparison.")
    st.caption("The app uses the existing Gemini model configuration and API key.")
    st.caption("Uploads and run artifacts are stored in this app's workspace, not committed to GitHub.")

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
        st.write(
            "Choose a screenplay ZIP, then select **Find promos**. The default hosted "
            "Gemini option uses the existing API key saved in Streamlit Secrets."
        )
        st.stop()
    upload_data = upload.getvalue()

try:
    source_path, upload_hash = store_upload(upload_data, ROOT / ".runtime" / "uploads")
    episodes, parser_warnings = load_show(source_path)
    if not episodes:
        raise ValueError("No episodes could be parsed. Check that the ZIP contains screenplay .txt files.")
    config = load_config(ROOT / CONFIG_FILE)
    config["cache_dir"] = str(ROOT / config["cache_dir"])
    config["raw_outputs_dir"] = str(ROOT / config["raw_outputs_dir"])
    source_fingerprint = show_fingerprint(episodes)
    label = ui_run_label(upload_hash, config, variant)
    output_dir = Path(config["raw_outputs_dir"]) / f"run_{label}"
except (ValueError, OSError, ProviderError) as exc:
    st.error(str(exc))
    st.stop()

st.markdown('<div class="section-kicker">02 / Input validation</div>', unsafe_allow_html=True)
st.caption(f"Parsed {len(episodes)} episode(s). Run {variant} will use the configured Gemini model.")
parser_stats = _screenplay_diagnostics(episodes)
stat_cols = st.columns(6)
stat_cols[0].metric("Scenes", parser_stats["scenes"])
stat_cols[1].metric("Dialogue blocks", parser_stats["dialogue"])
stat_cols[2].metric("Action blocks", parser_stats["actions"])
stat_cols[3].metric("Camera blocks", parser_stats["camera_blocks"])
stat_cols[4].metric("Cut / transition blocks", parser_stats["cut_blocks"])
stat_cols[5].metric("Inline cut cues", parser_stats["inline_cut_cues"])
st.caption("These counts confirm what the parser extracted from the screenplay. Review warnings and source evidence before trusting a cue.")
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
st.markdown('<div class="section-kicker">03 / Results overview</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="section-kicker">04 / Promo explorer</div>', unsafe_allow_html=True)
    st.subheader("Director’s editorial review")
    options = [f"#{c['rank']} — {c['candidate_id']} — {c['duration_seconds']:.1f}s" for c in ranked]
    choice = st.selectbox("Choose a promo to inspect", options)
    candidate = ranked[options.index(choice)]
    st.write("**Hook:**", candidate["hook"]["quote"])
    st.write("**Cut-out / unresolved question:**", candidate["cliffhanger"])
    st.write("**Director’s editorial read:**", candidate["why_it_works"])
    st.write("**First-time viewer context:**", candidate["context_needed"])
    with st.expander("Scripted shot, cut and story evidence", expanded=True):
        st.caption("Source-timed blocks inside or crossing this promo's segment boundaries. Camera and cut entries are screenplay cues, not proof of the finished footage.")
        evidence_rows = _promo_source_rows(candidate, episodes)
        if evidence_rows:
            st.dataframe(evidence_rows, hide_index=True, use_container_width=True)
        else:
            st.info("No timed source events were found inside these selected segments.")
        visual_rows = [row for row in evidence_rows if row["Cue type"] in ("Camera", "Transition") or row["Cut cue"]]
        if visual_rows:
            st.markdown("**Authored camera / edit cues**")
            st.dataframe([{key: row[key] for key in ("Episode", "Scene", "Time", "Cue type", "Source label", "Cut cue", "Screenplay evidence")} for row in visual_rows], hide_index=True, use_container_width=True)
        else:
            st.caption("No explicit camera or transition cue was parsed in this promo window; the model must not invent one.")
    with st.expander("Score breakdown"):
        st.json(candidate["score_breakdown"], expanded=False)
    with st.expander("Footage QC — checks that require the actual video"):
        st.markdown(
            "- **Aspect ratio and pixels:** verify the delivered frame ratio and native resolution "
            "against the publishing brief; screenplay text contains no pixel dimensions.\n"
            "- **Vertical reframing and safe zones:** check that faces, eyelines and critical action "
            "remain visible after the platform crop and interface overlays.\n"
            "- **Camera and subject motion:** inspect the actual movement, direction, stability, focus "
            "and whether the action reads on a phone screen.\n"
            "- **Transitions and continuity:** verify the real cut, match, movement continuity and "
            "audio hand-off between adjacent shots.\n"
            "- **Character / climax reveal:** confirm the intended identity or payoff is visibly "
            "legible, while the promo still cuts before the story question is resolved."
        )
        st.caption(
            "The narrative reveal logic above is screenplay-derived. This checklist is a human "
            "footage-review handoff, not an automated video inspection."
        )
    with st.expander("View the screenplay for this promo"):
        st.caption("Source blocks that cross a cut are marked; the full block may extend outside the clip.")
        st.code("\n".join(script_lines(candidate, episodes)), language=None)

    st.markdown('<div class="section-kicker">05 / Editorial review</div>', unsafe_allow_html=True)
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

st.markdown('<div class="section-kicker">06 / Export</div>', unsafe_allow_html=True)
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
