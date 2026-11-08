"""Kuku Promo Editorial Desk — Streamlit interface."""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import streamlit as st

from compare_runs import compare
from llm_client import ProviderError, load_config
from run_stage4 import execute, show_fingerprint
from screenplay import load_show, show_counts
from stage4_logic import source_events
from ui_helpers import (
    load_reviews,
    review_csv,
    review_path,
    save_review,
    script_lines,
    store_upload,
    table_rows,
    ui_run_label,
)


ROOT = Path(__file__).resolve().parent
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


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _parse_rationale(text: str) -> dict[str, str]:
    """Split the prompt's labelled editorial rationale, with a safe fallback."""
    labels = [
        "Promo angle",
        "Beat progression",
        "Character / power",
        "Shot / cut progression",
        "Reveal control",
        "Cut-out decision",
    ]
    if not isinstance(text, str) or not text.strip():
        return {}
    escaped = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"(?:^|;\s*)({escaped})\s*:\s*", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return {"Editorial rationale": text.strip()}
    parsed: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parsed[match.group(1).strip().title()] = text[start:end].strip(" ;\n")
    return parsed


def _duration(candidate: dict) -> float:
    try:
        return float(candidate.get("duration_seconds", 0))
    except (TypeError, ValueError):
        return 0.0


def _candidate_score(candidate: dict) -> str:
    value = candidate.get("final_score", candidate.get("base_score"))
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _segment_label(candidate: dict) -> str:
    segments = candidate.get("segments", [])
    if not segments:
        return "Source segment unavailable"
    if len(segments) == 1:
        return f"EPISODE {segments[0].get('episode', '—')}"
    eps = " → ".join(str(seg.get("episode", "—")) for seg in segments)
    return f"CROSS-EPISODE · {eps}"


def _render_editorial_card(label: str, body: str, index: int) -> None:
    if not body:
        body = "Not established by the selected screenplay evidence."
    st.markdown(
        f"""<div class="editorial-card">
          <div class="editorial-card-index">{index:02d}</div>
          <div class="editorial-card-copy">
            <div class="editorial-card-label">{_escape(label)}</div>
            <div class="editorial-card-body">{_escape(body)}</div>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="Kuku | Promo Editorial Desk", page_icon="🎬", layout="wide")

# Reuse the existing Gemini API key. No new key or provider is introduced.
try:
    _existing_gemini_key = st.secrets.get("GEMINI_API_KEY")
except Exception:
    _existing_gemini_key = None
if _existing_gemini_key:
    os.environ.setdefault("GEMINI_API_KEY", str(_existing_gemini_key))

st.markdown(
    """
    <style>
    :root {
      color-scheme: dark;
      --bg: #09090d;
      --panel: #121117;
      --panel2: #19151d;
      --line: #332936;
      --accent: #e7a0bd;
      --accent2: #f0c2d4;
      --warm: #d7b48b;
      --text: #f4eef2;
      --muted: #aaa0aa;
      --green: #a4c9b3;
    }
    .stApp {
      background:
        radial-gradient(ellipse at 8% 0%, rgba(166, 84, 122, .14), transparent 34%),
        radial-gradient(ellipse at 100% 12%, rgba(115, 78, 105, .10), transparent 31%),
        var(--bg);
      color: var(--text);
    }
    [data-testid="stHeader"] { background: rgba(9,9,13,.82); }
    [data-testid="stSidebar"] { background:#0e0d12; border-right:1px solid var(--line); }
    [data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3 { color:var(--accent2); }
    [data-testid="stAppViewContainer"] .main .block-container { max-width:1320px; padding-top:1.6rem; padding-bottom:3.5rem; }
    h1,h2,h3 { color:var(--accent2) !important; letter-spacing:-.035em; }
    p,label,li { color:var(--text); }
    [data-testid="stCaptionContainer"] { color:var(--muted) !important; }
    .masthead { display:flex; justify-content:space-between; align-items:center; gap:1rem; padding:.2rem 0 .85rem; border-bottom:1px solid var(--line); margin-bottom:1.15rem; }
    .brand-lockup { display:flex; align-items:center; gap:.65rem; color:#eee2e9; font-size:.72rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
    .brand-mark { display:grid; place-items:center; width:30px; height:30px; border:1px solid rgba(231,160,189,.6); border-radius:9px; color:var(--accent2); font-size:.9rem; }
    .mast-meta { color:#a99ca7; font-size:.65rem; letter-spacing:.14em; text-transform:uppercase; text-align:right; }
    .hero { padding:1.75rem 1.85rem 1.5rem; border:1px solid rgba(231,160,189,.27); border-radius:20px; background:linear-gradient(125deg,rgba(43,28,39,.96),rgba(17,15,22,.98) 58%,rgba(34,23,34,.94)); box-shadow:0 20px 60px rgba(0,0,0,.25); margin-bottom:1.1rem; }
    .eyebrow { color:var(--warm); font-size:.66rem; letter-spacing:.2em; font-weight:800; text-transform:uppercase; margin-bottom:.8rem; }
    .hero-title { color:#fff8fb; font-size:clamp(2.1rem,4.2vw,3.6rem); line-height:1.01; font-weight:800; letter-spacing:-.06em; max-width:900px; }
    .hero-title em { color:var(--accent); font-style:normal; }
    .hero-desc { max-width:820px; color:#c9bdc7; font-size:.98rem; line-height:1.65; margin-top:.85rem; }
    .hero-footer { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; margin-top:1.25rem; }
    .hero-tag { border:1px solid rgba(231,160,189,.25); background:rgba(255,255,255,.035); color:#e7d2dd; padding:.38rem .65rem; border-radius:6px; font-size:.62rem; font-weight:750; letter-spacing:.1em; }
    .section-head { display:flex; align-items:center; gap:.65rem; margin:1.35rem 0 .75rem; color:var(--accent); font-size:.68rem; font-weight:850; letter-spacing:.17em; text-transform:uppercase; }
    .section-head:after { content:""; height:1px; flex:1; background:linear-gradient(90deg,var(--line),transparent); }
    .principle { min-height:133px; padding:1rem 1.05rem; border:1px solid var(--line); border-radius:13px; background:linear-gradient(145deg,#17141c,#101015); }
    .principle-no { color:var(--warm); font-size:.62rem; letter-spacing:.16em; font-weight:800; }
    .principle-title { color:#f5e9ef; font-size:1rem; font-weight:800; margin:.42rem 0 .3rem; }
    .principle-copy { color:#afa4ae; font-size:.78rem; line-height:1.5; }
    .meta-strip { display:flex; flex-wrap:wrap; gap:.45rem; margin:.4rem 0 .8rem; }
    .meta-chip { border:1px solid #493544; color:#e4cbd8; padding:.32rem .55rem; border-radius:5px; font-size:.65rem; letter-spacing:.06em; background:#17131a; }
    [data-testid="stMetric"] { padding:.85rem .95rem; border:1px solid var(--line); border-radius:12px; background:linear-gradient(145deg,#17141c,#101015); }
    [data-testid="stMetricLabel"] { color:#baabb7 !important; font-size:.72rem !important; }
    [data-testid="stMetricValue"] { color:var(--accent2) !important; font-size:1.65rem !important; }
    div.stButton>button,div.stDownloadButton>button,div[data-testid="stFormSubmitButton"]>button { border:1px solid rgba(231,160,189,.5); border-radius:9px; background:linear-gradient(135deg,#e7a0bd,#cf83a5); color:#21141c; font-weight:800; min-height:2.55rem; }
    div.stButton>button:hover,div.stDownloadButton>button:hover,div[data-testid="stFormSubmitButton"]>button:hover { border-color:#f4bed5; filter:brightness(1.07); color:#160e14; }
    div[data-testid="stFileUploader"] section { border:1px dashed rgba(231,160,189,.45); border-radius:14px; background:rgba(231,160,189,.035); }
    [data-testid="stAlert"] { border:1px solid rgba(231,160,189,.25); border-radius:11px; background:rgba(231,160,189,.045); }
    [data-testid="stExpander"] { border:1px solid var(--line); border-radius:11px; background:rgba(255,255,255,.014); }
    [data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:11px; overflow:hidden; }
    div[data-baseweb="select"]>div,div[data-baseweb="input"]>div,div[data-baseweb="textarea"]>div { background:#17141c; border-color:#493342; }
    div[data-testid="stTabs"] button { color:#cdbdc9; font-weight:700; }
    div[data-testid="stTabs"] button[aria-selected="true"] { color:var(--accent2); border-bottom-color:var(--accent); }
    .editorial-card { display:flex; gap:.85rem; padding:.85rem .95rem; margin:.45rem 0; border:1px solid var(--line); border-radius:11px; background:linear-gradient(110deg,#17141c,#111016); }
    .editorial-card-index { color:var(--warm); font-size:.7rem; font-weight:850; letter-spacing:.12em; padding-top:.1rem; min-width:22px; }
    .editorial-card-label { color:var(--accent2); font-size:.68rem; font-weight:850; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.25rem; }
    .editorial-card-body { color:#d6cdd4; font-size:.88rem; line-height:1.55; }
    .candidate-card { border:1px solid var(--line); border-radius:13px; padding:1rem 1.05rem; margin:.45rem 0; background:linear-gradient(115deg,#19151e,#111016); }
    .candidate-topline { display:flex; justify-content:space-between; gap:.7rem; align-items:center; color:#bbaab7; font-size:.65rem; font-weight:800; letter-spacing:.13em; text-transform:uppercase; }
    .candidate-hook { color:#fff5fa; font-size:1rem; line-height:1.45; font-weight:650; margin:.7rem 0; }
    .candidate-foot { color:#baadb8; font-size:.72rem; display:flex; gap:.9rem; flex-wrap:wrap; }
    .callout { border-left:3px solid var(--accent); padding:.7rem .9rem; background:rgba(231,160,189,.055); color:#e4d8e0; border-radius:0 8px 8px 0; line-height:1.55; font-size:.88rem; }
    .qc-note { border:1px solid rgba(215,180,139,.28); background:rgba(215,180,139,.055); border-radius:10px; padding:.8rem .95rem; color:#ddcdb9; font-size:.82rem; line-height:1.55; }
    hr { border-color:var(--line); }
    @media(max-width:700px){ .hero{padding:1.2rem;border-radius:14px}.mast-meta{display:none}.hero-desc{font-size:.88rem}.principle{min-height:auto} }
    </style>

    <div class="masthead">
      <div class="brand-lockup"><span class="brand-mark">K</span><span>KUKU · PROMO EDITORIAL DESK</span></div>
      <div class="mast-meta">SCRIPT INTELLIGENCE&nbsp;&nbsp; / &nbsp;&nbsp;EDITORIAL DEVELOPMENT</div>
    </div>
    <div class="hero">
      <div class="eyebrow">Short-form drama · Editorial decision workspace</div>
      <div class="hero-title">Find the tension.<br><em>Protect the reveal.</em> Cut on the question.</div>
      <div class="hero-desc">A screenplay-first promo desk that evaluates dramatic construction, character stakes, authored camera and cut cues, reveal control and the final unresolved beat — then gives an editor source-timed evidence to review.</div>
      <div class="hero-footer">
        <span class="hero-tag">20–60 SEC PROMO WINDOWS</span>
        <span class="hero-tag">HOOK → ESCALATION → OPEN LOOP</span>
        <span class="hero-tag">SOURCE-GROUNDED CUT LOGIC</span>
        <span class="hero-tag">HUMAN EDITORIAL SIGN-OFF</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# A compact, visible editorial framework makes the product's judgement legible
# before a user uploads anything.
st.markdown('<div class="section-head">THE EDITORIAL LENS</div>', unsafe_allow_html=True)
principles = [
    ("01", "HOOK", "Win the first 3 seconds with a threat, contradiction, shock or compelling question."),
    ("02", "BUILD", "Create context, escalate the stakes and track the shift in character power."),
    ("03", "WITHHOLD", "Reveal enough to understand the conflict; protect the decisive answer or payoff."),
    ("04", "CUT", "Exit on an unresolved beat — not after the reveal, and not mid-thought."),
]
principle_cols = st.columns(4)
for col, (number, title, copy) in zip(principle_cols, principles):
    with col:
        st.markdown(
            f'<div class="principle"><div class="principle-no">{number} / EDITORIAL TEST</div><div class="principle-title">{title}</div><div class="principle-copy">{_escape(copy)}</div></div>',
            unsafe_allow_html=True,
        )

st.markdown('<div class="section-head">01 / SOURCE MATERIAL</div>', unsafe_allow_html=True)
left_intro, right_intro = st.columns([1.45, 1])
with left_intro:
    st.markdown("**Import a timestamped screenplay pack**")
    st.caption("Upload one ZIP containing episode-wise UTF-8 .txt screenplays. The parser reads scene, dialogue, action, camera and explicit edit / transition blocks.")
with right_intro:
    st.markdown('<div class="meta-strip"><span class="meta-chip">TIMED DIALOGUE</span><span class="meta-chip">ACTION BLOCKS</span><span class="meta-chip">CAMERA CUES</span><span class="meta-chip">CUT / TRANSITION CUES</span></div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### SESSION CONTROL")
    variant = st.radio("Run label", ["A", "B"], horizontal=True, help="A/B are independent runs for a stability comparison; they are not separate models.")
    st.divider()
    st.markdown("**MODEL**")
    st.caption("Gemini configuration · existing API key from Streamlit Secrets")
    st.markdown("**INPUT POLICY**")
    st.caption("Only upload screenplays you are authorized to send to the configured API. Do not commit confidential source files to GitHub.")
    st.divider()
    st.caption("KUKU · PROMO EDITORIAL DESK")

local_show = ROOT / "input_pack" / "show.zip"
upload_data = None
if local_show.is_file():
    source_choice = st.radio("Screenplay source", ["Use the show on this computer", "Upload another ZIP"], horizontal=True)
    if source_choice == "Use the show on this computer":
        try:
            upload_data = local_show.read_bytes()
        except OSError as exc:
            st.error(f"Could not read the local screenplay ZIP: {exc}")
            st.stop()
if upload_data is None:
    upload = st.file_uploader("Drop the screenplay ZIP here", type=["zip"], label_visibility="visible")
    if upload is None:
        st.markdown('<div class="callout">The workflow starts with source validation. Once uploaded, the desk will report what it successfully parsed before any model analysis begins.</div>', unsafe_allow_html=True)
        st.stop()
    upload_data = upload.getvalue()

try:
    source_path, upload_hash = store_upload(upload_data, ROOT / ".runtime" / "uploads")
    episodes, parser_warnings = load_show(source_path)
    if not episodes:
        raise ValueError("No episodes could be parsed. Check that the ZIP contains screenplay .txt files with the expected timestamped block structure.")
    config = load_config(ROOT / CONFIG_FILE)
    config["cache_dir"] = str(ROOT / config["cache_dir"])
    config["raw_outputs_dir"] = str(ROOT / config["raw_outputs_dir"])
    source_fingerprint = show_fingerprint(episodes)
    label = ui_run_label(upload_hash, config, variant)
    output_dir = Path(config["raw_outputs_dir"]) / f"run_{label}"
except (ValueError, OSError, ProviderError) as exc:
    st.error(str(exc))
    st.stop()

st.markdown('<div class="section-head">02 / SCRIPT BREAKDOWN</div>', unsafe_allow_html=True)
st.caption(f"Source recognised · {len(episodes)} episode(s) · Run {variant}")
parser_stats = _screenplay_diagnostics(episodes)
stat_cols = st.columns(6)
stat_cols[0].metric("Episodes", parser_stats["episodes"])
stat_cols[1].metric("Scenes", parser_stats["scenes"])
stat_cols[2].metric("Dialogue", parser_stats["dialogue"])
stat_cols[3].metric("Action", parser_stats["actions"])
stat_cols[4].metric("Camera", parser_stats["camera_blocks"])
stat_cols[5].metric("Cut cues", parser_stats["cut_blocks"] + parser_stats["inline_cut_cues"])
if parser_stats["cut_blocks"] + parser_stats["inline_cut_cues"] == 0:
    st.markdown('<div class="qc-note">No explicit cut / transition cue was detected in the parsed blocks. The model must not invent shot changes; inspect source labels and parser warnings.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="callout">Source diagnostics are a parser receipt, not a quality score. They confirm which timed elements reached the editorial analysis.</div>', unsafe_allow_html=True)
if parser_warnings:
    with st.expander(f"SOURCE VALIDATION · {len(parser_warnings)} parser warning(s)", expanded=True):
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
saved_choice = st.selectbox("Previously generated runs for this screenplay", list(saved)) if saved else None
if saved:
    st.caption("Open a previous result or launch a fresh model pass. A fresh run can take several minutes depending on script length and API limits.")
run_col, saved_col, _ = st.columns([1, 1, 2])
run_clicked = run_col.button("RUN EDITORIAL ANALYSIS", type="primary", use_container_width=True)
load_clicked = saved_col.button("OPEN SAVED CUTS", disabled=not saved, use_container_width=True)

if run_clicked:
    progress_bar = st.progress(0, text="Preparing source material")
    status_text = st.empty()

    def update_progress(info):
        phase, done, total = info["phase"], info["done"], max(1, info["total"])
        start, weight = {"episodes": (0.0, 0.60), "cross": (0.60, 0.10), "judge": (0.70, 0.29), "done": (1.0, 0.0)}[phase]
        progress_bar.progress(min(1.0, start + weight * done / total), text=info["message"])
        status_text.caption(f"{phase.capitalize()} · {done}/{total}")

    try:
        with st.spinner("Evaluating story beats, cut opportunities and reveal control…"):
            _, output_dir = execute(str(source_path), config, label, progress=update_progress)
        st.session_state["active_result"] = str(output_dir)
    except (ValueError, OSError, ProviderError) as exc:
        st.error(f"Analysis could not complete: {exc}")

if load_clicked:
    st.session_state["active_result"] = str(saved[saved_choice])

if not st.session_state.get("active_result"):
    st.stop()

try:
    output_dir = Path(st.session_state["active_result"])
    results = json.loads((output_dir / "promos.json").read_text(encoding="utf-8"))
    if results.get("show_fingerprint") != source_fingerprint:
        st.info("Choose a saved run for this screenplay or start a fresh analysis.")
        st.stop()
except (OSError, ValueError) as exc:
    st.error(f"Could not read editorial results: {exc}")
    st.stop()

metrics = results.get("metrics", {})
ranked = results.get("ranked", [])
active_label = results.get("run_label", label)

st.markdown('<div class="section-head">03 / EDITORIAL SHORTLIST</div>', unsafe_allow_html=True)
if results.get("status") != "completed":
    st.warning("This run is partial. Review processing coverage and unscored candidates before relying on the ranking.")
else:
    st.success("Analysis complete · shortlist ready for human editorial sign-off.")
summary_cols = st.columns(4)
summary_cols[0].metric("Episodes processed", f"{metrics.get('episodes_processed', 0)} / {metrics.get('episodes_total', len(episodes))}")
summary_cols[1].metric("Validated windows", metrics.get("validated", 0))
summary_cols[2].metric("Ranked promos", len(ranked))
summary_cols[3].metric("Run status", str(results.get("status", "unknown")).upper())

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
        st.caption(f"Stability check · {comparison['exact_matches']} exact top-10 overlaps · Jaccard {overlap if overlap is not None else 'N/A'}%. This measures repeatability, not editorial quality.")
    except (OSError, ValueError, KeyError, TypeError):
        st.caption("The saved runs could not be compared with the same settings.")

if not ranked:
    st.warning("No ranked promos were produced. Check the run report and unscored candidate log.")
else:
    st.markdown("**Ranked cut opportunities**")
    st.caption("Select a candidate below to inspect its editorial rationale and the exact screenplay evidence behind it.")
    options = [f"#{candidate.get('rank', index + 1):02d}  ·  {candidate.get('candidate_id', 'candidate')}  ·  {_duration(candidate):.1f}s  ·  {_segment_label(candidate)}" for index, candidate in enumerate(ranked)]
    choice = st.selectbox("Selected promo candidate", options, label_visibility="collapsed")
    candidate = ranked[options.index(choice)]

    # Shortlist cards make the ranking legible at a glance, without pretending
    # the scores replace an editor's judgement.
    preview = ranked[:min(4, len(ranked))]
    preview_cols = st.columns(min(2, len(preview)))
    for index, item in enumerate(preview):
        with preview_cols[index % len(preview_cols)]:
            hook = item.get("hook", {})
            hook_text = hook.get("quote", "Hook evidence unavailable") if isinstance(hook, dict) else str(hook)
            st.markdown(
                f'''<div class="candidate-card">
                  <div class="candidate-topline"><span>RANK #{_escape(item.get('rank', index+1))} · {_escape(_segment_label(item))}</span><span>{_duration(item):.1f} SEC</span></div>
                  <div class="candidate-hook">“{_escape(hook_text)}”</div>
                  <div class="candidate-foot"><span>EDITORIAL SCORE · {_candidate_score(item)}</span><span>{_escape(item.get('candidate_id', ''))}</span></div>
                </div>''',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="section-head">04 / SELECTED CUT · DIRECTOR’S REVIEW</div>', unsafe_allow_html=True)
    hook_obj = candidate.get("hook", {})
    hook_quote = hook_obj.get("quote", "Not available") if isinstance(hook_obj, dict) else str(hook_obj)
    segments = candidate.get("segments", [])
    meta = st.columns([1, 1, 1, 1])
    meta[0].metric("Rank", f"#{candidate.get('rank', '—')}")
    meta[1].metric("Duration", f"{_duration(candidate):.1f}s")
    meta[2].metric("Editorial score", _candidate_score(candidate))
    meta[3].metric("Segments", len(segments))

    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("**OPENING HOOK**")
        st.markdown(f'<div class="callout">{_escape(hook_quote)}</div>', unsafe_allow_html=True)
    with right:
        st.markdown("**UNRESOLVED CUT-OUT**")
        st.markdown(f'<div class="callout">{_escape(candidate.get("cliffhanger", "Not supplied"))}</div>', unsafe_allow_html=True)

    rationale = _parse_rationale(candidate.get("why_it_works", ""))
    tab_story, tab_shots, tab_scores, tab_qc = st.tabs([
        "EDITORIAL BREAKDOWN",
        "SHOT & CUT EVIDENCE",
        "SCORING & VALIDATION",
        "FOOTAGE QC HANDOFF",
    ])
    with tab_story:
        for idx, key in enumerate(["Promo Angle", "Beat Progression", "Character / Power", "Reveal Control", "Cut-Out Decision"], start=1):
            if key in rationale:
                _render_editorial_card(key, rationale[key], idx)
        if "Editorial rationale" in rationale:
            _render_editorial_card("Model's editorial rationale", rationale["Editorial rationale"], 1)
        st.markdown("**FIRST-TIME VIEWER CONTEXT**")
        context = candidate.get("context_needed", "Not specified")
        st.markdown(f'<div class="callout">{_escape(context)}</div>', unsafe_allow_html=True)
        if rationale.get("Shot / Cut Progression"):
            _render_editorial_card("Shot / Cut Progression", rationale["Shot / Cut Progression"], 6)
    with tab_shots:
        evidence_rows = _promo_source_rows(candidate, episodes)
        visual_rows = [row for row in evidence_rows if row["Cue type"] in ("Camera", "Transition") or row["Cut cue"]]
        st.markdown("**AUTHORED CAMERA / EDIT DIRECTIONS**")
        if visual_rows:
            st.dataframe(
                [{key: row[key] for key in ("Segment", "Episode", "Scene", "Time", "Cue type", "Source label", "Cut cue", "Screenplay evidence")} for row in visual_rows],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("No explicit camera or transition cue was parsed inside this promo window. The editorial model must not invent one.")
        st.markdown("**COMPLETE TIMED SOURCE BEATS**")
        if evidence_rows:
            st.dataframe(evidence_rows, hide_index=True, use_container_width=True)
        else:
            st.info("No timed source events were found inside these selected segments.")
        with st.expander("Read the selected screenplay passage"):
            st.code("\n".join(script_lines(candidate, episodes)), language=None)
    with tab_scores:
        st.markdown("**EDITORIAL SCORE CONTRIBUTION**")
        st.json(candidate.get("score_breakdown", {}), expanded=True)
        st.caption("Scores rank candidates against configured criteria. They are not an audience-performance prediction or a substitute for editor approval.")
        with st.expander("Processing and candidate validation details"):
            st.json({"run_status": results.get("status"), "metrics": metrics, "candidate_id": candidate.get("candidate_id"), "segments": segments}, expanded=False)
    with tab_qc:
        st.markdown("**SCRIPT-DERIVED VS FOOTAGE-VERIFIED**")
        st.markdown('<div class="qc-note">This prototype analyses timestamped screenplay material. It can reason about authored camera and transition instructions, but cannot confirm how the final rendered video looks or behaves.</div>', unsafe_allow_html=True)
        qc_cols = st.columns(2)
        with qc_cols[0]:
            st.markdown("**Can be evaluated from this screenplay**")
            st.markdown("- Dramatic hook and escalation\n- Character introduction and information flow\n- Written camera / shot directions\n- Explicit CUT TO / MATCH CUT / transition cues\n- Reveal, withheld payoff and spoiler risk\n- Source-timed candidate boundaries")
        with qc_cols[1]:
            st.markdown("**Requires the rendered footage**")
            st.markdown("- Actual aspect ratio and pixel resolution\n- Composition, framing and safe zones\n- Executed transitions and shot continuity\n- Actual camera / subject motion and focus\n- Whether a visual identity or climax reveal reads on screen")
        st.caption("No motion, pixel or framing judgement is claimed without video evidence.")

    st.markdown('<div class="section-head">05 / EDITOR SIGN-OFF</div>', unsafe_allow_html=True)
    st.caption("Keep model suggestions separate from your own editorial verdict. Your review is saved separately from raw model output.")
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
        verdict_col, notes_col = st.columns([1, 2])
        with verdict_col:
            verdict = st.radio("Editorial verdict", verdicts, index=verdicts.index(previous))
        with notes_col:
            notes = st.text_area("Editor notes", value=current.get("notes", ""), placeholder="e.g. Strong hook; reveal is too complete; move cut-out to the interrupted reaction.")
        submitted = st.form_submit_button("SAVE EDITORIAL VERDICT")
    if submitted:
        try:
            reviews = save_review(review_file, candidate["candidate_id"], verdict, notes)
            st.success("Editorial verdict saved in this app workspace.")
        except (OSError, ValueError) as exc:
            st.error(f"Could not save editorial verdict: {exc}")
    top = ranked[:10]
    reviewed = sum(reviews.get(item["candidate_id"], {}).get("verdict") in ("Good", "Bad", "Unsure") for item in top)
    st.caption(f"TOP-10 AUDIT · {reviewed} / {len(top)} candidates reviewed")
    st.download_button("EXPORT EDITORIAL AUDIT", data=review_csv(ranked, reviews), file_name=f"run_{active_label}_user_audit.csv", mime="text/csv")

st.markdown('<div class="section-head">06 / EXPORT & RUN RECORD</div>', unsafe_allow_html=True)
with st.expander("Download source analysis and run records", expanded=False):
    for filename, label_text, mime in (
        ("promos.csv", "Download ranked CSV", "text/csv"),
        ("promos.json", "Download raw JSON", "application/json"),
        ("eval_report.md", "Download evaluation report", "text/markdown"),
        ("manual_audit.md", "Download manual audit", "text/markdown"),
        ("manual_audit.csv", "Download blank audit template", "text/csv"),
        ("ranked_report.md", "Download ranked report", "text/markdown"),
    ):
        path = output_dir / filename
        if path.is_file():
            st.download_button(label_text, data=path.read_bytes(), file_name=filename, mime=mime, key=f"download_{filename}")

if results.get("unscored"):
    st.caption(f"{len(results['unscored'])} structurally valid candidates could not be scored; details remain in the run JSON.")
