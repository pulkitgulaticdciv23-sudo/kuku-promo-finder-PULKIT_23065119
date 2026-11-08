"""Write private reports, CSV/JSON exports and a human audit sheet."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from llm_client import atomic_write
from screenplay import time_to_us
from stage4_logic import SCORE_KEYS, source_events, stable_key
from validators import _unsafe_reasons, events_after_cut


def _write(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def agreement(candidates: list[dict], judgements: dict[str, dict]) -> dict:
    differences = []
    paired = 0
    for candidate in candidates:
        self_scores = candidate.get("self_scores")
        judge = judgements.get(candidate["candidate_id"])
        if not self_scores or not judge:
            continue
        paired += 1
        for key in SCORE_KEYS:
            differences.append(abs(self_scores[key] - judge["scores"][key]["score"]))
    if not differences:
        return {"paired_candidates": 0, "comparisons": 0, "mean_absolute_difference": None,
                "exact_agreement_percent": None, "within_one_percent": None}
    return {"paired_candidates": paired, "comparisons": len(differences),
            "mean_absolute_difference": round(sum(differences) / len(differences), 2),
            "exact_agreement_percent": round(100 * sum(d == 0 for d in differences) / len(differences), 1),
            "within_one_percent": round(100 * sum(d <= 1 for d in differences) / len(differences), 1)}


def metrics(state: dict, episodes: dict[int, dict], ranked: list[dict], removed: list[dict]) -> dict:
    proposed = sum(entry.get("proposed", 0) for entry in state["episode_passes"] + state["cross_passes"])
    candidates = state["candidates"]
    hooks_in_window, dialogue_cuts, ellipsis_cuts = 0, 0, 0
    for c in candidates:
        first = c["segments"][0]
        if first["episode"] == c["hook"]["episode"]:
            offset = time_to_us(c["hook"]["timestamp"]) - time_to_us(first["start"])
            hooks_in_window += int(0 <= offset < 3_000_000)
        for s in c["segments"]:
            for field in ("start", "end"):
                reasons = _unsafe_reasons(episodes[s["episode"]], time_to_us(s[field]))
                dialogue_cuts += int(any("inside dialogue" in x for x in reasons))
                ellipsis_cuts += int(any("ellipsis" in x for x in reasons))
    jmap = {j["candidate_id"]: j for j in state["judgements"]}
    return {
        "episodes_total": len(episodes), "episodes_attempted": len(state["episode_passes"]),
        "episodes_processed": sum(entry.get("status") == "processed" for entry in state["episode_passes"]),
        "proposed": proposed, "validated": len(candidates),
        "validation_pass_percent": round(100 * len(candidates) / proposed, 1) if proposed else None,
        "ranked": len(ranked), "deduplicated": len(removed),
        "judged": len(jmap), "unjudged": sum(c["candidate_id"] not in jmap for c in candidates),
        "average_duration_seconds": round(sum(c["duration_seconds"] for c in candidates) / len(candidates), 2) if candidates else None,
        "hooks_within_three_seconds": hooks_in_window,
        "hook_rate_percent": round(100 * hooks_in_window / len(candidates), 1) if candidates else None,
        "mid_dialogue_cut_count": dialogue_cuts, "obvious_ellipsis_cut_count": ellipsis_cuts,
        "self_vs_judge": agreement(candidates, jmap),
    }


def _csv_text(rows: list[dict], headers: list[str]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_safe(row.get(key, "")) for key in headers})
    return "\ufeff" + buffer.getvalue()


def write_outputs(out_dir: Path, state: dict, episodes: dict[int, dict], ranked: list[dict],
                  unscored: list[dict], removed: list[dict]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = metrics(state, episodes, ranked, removed)
    payload = {"status": state["status"], "run_label": state["run_label"], "show_fingerprint": state["show_fingerprint"],
               "generation_settings": state["generation_settings"], "metrics": m,
               "ranked": ranked, "unscored": unscored, "deduplicated": removed}
    _write(out_dir / "promos.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write(out_dir / "run_state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    csv_rows = []
    for c in ranked:
        row = {"status": "ranked", "rank": c["rank"], "candidate_id": c["candidate_id"], "episodes": ", ".join(str(s["episode"]) for s in c["segments"]),
               "segments": json.dumps(c["segments"], ensure_ascii=False), "duration_seconds": c["duration_seconds"],
               "hook": c["hook"]["quote"], "cliffhanger": c["cliffhanger"], "why_it_works": c["why_it_works"],
               "context_needed": c["context_needed"], "base_score": c["base_score"], "diversity_penalty": c["diversity_penalty"],
               "final_score": c["final_score"], "score_breakdown": json.dumps(c["score_breakdown"], ensure_ascii=False),
               "judge_justifications": json.dumps({k: c["judge"]["scores"][k]["justification"] for k in SCORE_KEYS}, ensure_ascii=False)}
        for key in SCORE_KEYS:
            row[key] = c["judge"]["scores"][key]["score"]
        csv_rows.append(row)
    for c in unscored:
        csv_rows.append({"status": "unscored", "candidate_id": c["candidate_id"],
                         "episodes": ", ".join(str(s["episode"]) for s in c["segments"]),
                         "segments": json.dumps(c["segments"], ensure_ascii=False), "duration_seconds": c["duration_seconds"],
                         "hook": c["hook"]["quote"], "cliffhanger": c["cliffhanger"],
                         "why_it_works": c["why_it_works"], "context_needed": c["context_needed"]})
    headers = ["status", "rank", "candidate_id", "episodes", "segments", "duration_seconds", "hook", "cliffhanger", "why_it_works",
               "context_needed", "base_score", "diversity_penalty", "final_score", *SCORE_KEYS, "score_breakdown", "judge_justifications"]
    _write(out_dir / "promos.csv", _csv_text(csv_rows, headers))

    lines = ["# Ranked promo report", "", f"Run: {state['run_label']} | Status: {state['status']}",
             f"Processed episodes: {m['episodes_processed']}/{m['episodes_total']}; ranked promos: {m['ranked']}.",
             "", "Ranking is a screening aid. Model judgments and story claims need human verification.",
             "The same model proposes and judges. Later-twist spoilers are not fully knowable from the judge's visible clips.", "",
             "| Rank | Candidate | Segments | Seconds | Final / 100 | Hook |", "| ---: | --- | --- | ---: | ---: | --- |"]
    for c in ranked:
        segments = "; ".join(f"E{s['episode']} {s['start']}–{s['end']}" for s in c["segments"])
        lines.append(f"| {c['rank']} | {c['candidate_id']} | {_cell(segments)} | {c['duration_seconds']:.1f} | {c['final_score']:.2f} | {_cell(c['hook']['quote'])} |")
    if not ranked:
        lines.append("| — | No judged and validated promos to rank | — | — | — | — |")
    lines.extend(["", "## Why these appear first", ""])
    for c in ranked[:5]:
        j = c["judge"]["scores"]
        lines += [f"### {c['rank']}. {c['candidate_id']}",
                  f"- The model rated the hook {j['hook_strength']['score']}/10, ending {j['cliffhanger_strength']['score']}/10, "
                  f"and standalone clarity {j['standalone_clarity']['score']}/10. These are not independent human ratings.",
                  f"- Duration is {c['duration_seconds']:.1f}s; base score {c['base_score']:.2f}, "
                  f"diversity penalty {c['diversity_penalty']:.2f}, final score {c['final_score']:.2f}.",
                  f"- Model's stated cliffhanger: {c['cliffhanger']}",
                  f"- Model's reason: {c['why_it_works']}", ""]
    _write(out_dir / "ranked_report.md", "\n".join(lines) + "\n")

    a = m["self_vs_judge"]
    eval_lines = ["# Evaluation report", "", f"Run: {state['run_label']} | Status: {state['status']}",
                  f"Episodes processed: {m['episodes_processed']}/{m['episodes_total']}",
                  f"Model-proposed candidates: {m['proposed']}; passed structural checks: {m['validated']}; "
                  f"pass rate: {m['validation_pass_percent'] if m['validation_pass_percent'] is not None else 'N/A'}%.",
                  f"Judged: {m['judged']}; unjudged: {m['unjudged']}; removed as overlapping: {m['deduplicated']}; ranked: {m['ranked']}.",
                  f"Average accepted duration: {m['average_duration_seconds'] if m['average_duration_seconds'] is not None else 'N/A'} seconds.",
                  f"Hook starts within first 3 seconds: {m['hooks_within_three_seconds']}/{m['validated']} "
                  f"({m['hook_rate_percent'] if m['hook_rate_percent'] is not None else 'N/A'}%).",
                  f"Cuts inside timed dialogue: {m['mid_dialogue_cut_count']}; obvious ellipsis-continuation cuts: {m['obvious_ellipsis_cut_count']}.",
                  "These are structural checks. They do not establish hook quality or detect every split sentence.", "",
                  "## Self-assessment versus judge", "",
                  f"Comparable candidates: {a['paired_candidates']}; individual score comparisons: {a['comparisons']}.",
                  f"Mean absolute score difference: {a['mean_absolute_difference'] if a['mean_absolute_difference'] is not None else 'N/A'}; "
                  f"exact agreement: {a['exact_agreement_percent'] if a['exact_agreement_percent'] is not None else 'N/A'}%; "
                  f"within one point: {a['within_one_percent'] if a['within_one_percent'] is not None else 'N/A'}%.",
                  "These are two assessments by the same configured model, not independent agreement with a human.", "",
                  "## Run-to-run stability", "", "Not available from one run. Run A and B independently, then execute compare_runs.py.", "",
                  "## Detected issues from this run", ""]
    issues = Counter(err for item in state["rejections"] for err in item.get("errors", []))
    for reason, count in issues.most_common():
        eval_lines.append(f"- {count} rejection(s): {reason}")
    for error in state["errors"]:
        eval_lines.append(f"- Execution error: {error}")
    if removed:
        eval_lines.append(f"- {len(removed)} overlapping candidate(s) were removed by the configured threshold.")
    if state["parser_warnings"]:
        eval_lines.append(f"- Source parser reported {len(state['parser_warnings'])} warning(s); see run_state.json.")
    if any(any("action hook" in w for w in c["validation_warnings"]) for c in state["candidates"]):
        eval_lines.append("- At least one accepted candidate uses an action hook; its visual impact needs review.")
    if not issues and not state["errors"] and not removed and not state["parser_warnings"]:
        eval_lines.append("- No automatic rejection or parser warning was observed in this run.")
    usage = state.get("usage")
    eval_lines += ["", "## Token usage", ""]
    if usage and usage["with_provider_usage"]:
        eval_lines += [f"Provider-reported logical responses with token counts: {usage['with_provider_usage']}; "
                       f"without counts: {usage['missing_provider_usage']}.",
                       f"Logical input/output tokens, including cache hits: {usage['input_tokens']}/{usage['output_tokens']}.",
                       f"New-request input/output tokens: {usage['new_input_tokens']}/{usage['new_output_tokens']}."]
        if m["episodes_processed"] and state["status"] == "completed" and usage["missing_provider_usage"] == 0:
            for target in (50, 100):
                scale = target / m["episodes_processed"]
                eval_lines.append(f"Rough linear estimate for {target} episodes: "
                                  f"{round(usage['input_tokens'] * scale)} input and {round(usage['output_tokens'] * scale)} output tokens.")
            eval_lines.append("Estimates assume similar episode length, acceptance, judging and batch counts; actual usage can differ greatly.")
        else:
            eval_lines.append("No full-show extrapolation: the run is partial or some responses lack provider usage counts.")
    else:
        eval_lines.append("N/A: the provider did not supply token counts for this run.")
    eval_lines += ["Local inference has no per-token API charge; model download, hardware and electricity still have costs.",
                   "For cloud models, calculate input tokens × current input price plus output tokens × current output price; account quotas and fees vary.",
                   "", "## Limits", "", "Text cannot establish acting, music, editing rhythm, visual impact, or whether video timestamps match the scripts.",
                   "The candidate menu and cross-episode batches are limited, and can miss better combinations.", ""]
    _write(out_dir / "eval_report.md", "\n".join(eval_lines))
    failure_lines = ["# Detected failures in this run", "", "Automatically observed; human review can uncover additional issues.", ""]
    for reason, count in issues.most_common():
        failure_lines.append(f"- {count} rejection(s): {reason}")
    for error in state["errors"]:
        failure_lines.append(f"- Execution error: {error}")
    for item in removed:
        failure_lines.append(f"- Overlap: {item['candidate_id']} duplicated {item['kept_candidate_id']} (IoU {item['overlap_iou']}).")
    if not issues and not state["errors"] and not removed:
        failure_lines.append("- No automatic rejection or duplicate was observed.")
    _write(out_dir / "WHERE_AI_GOT_IT_WRONG.md", "\n".join(failure_lines) + "\n")

    audit = ["# Manual audit: top 10", "", "Fill in the blank verdict and notes for each promo. Script text is confidential.",
             "Events marked OVERLAPS touch a cut; their full source descriptions may extend outside the selected footage.", ""]
    audit_rows = []
    for c in ranked[:10]:
        audit += [f"## Rank {c['rank']} — {c['candidate_id']}", "", "Verdict (good/bad): ____________________", "Notes: ____________________", ""]
        for index, seg in enumerate(c["segments"], 1):
            start, end = time_to_us(seg["start"]), time_to_us(seg["end"])
            audit += [f"### Segment {index}: E{seg['episode']} {seg['start']}–{seg['end']}", ""]
            for e in source_events(episodes[seg["episode"]], seg, include_overlaps=True):
                marker = "INSIDE" if e["start_us"] >= start and e["end_us"] <= end else "OVERLAPS"
                audit.append(f"- [{marker}] {e['start']}–{e['end']} {e['type']} {e.get('speaker') or ''}: {e['text']}")
            audit.append("")
        last = c["segments"][-1]
        audit += ["**Immediately after the final cut — not in the promo:**", ""]
        for e in events_after_cut(episodes[last["episode"]], last["end"], window_seconds=5):
            if e["start_us"] >= time_to_us(last["end"]):
                audit.append(f"- {e['start']}–{e['end']} {e['type']} {e.get('speaker') or ''}: {e['text']}")
        audit.append("")
        audit_rows.append({"rank": c["rank"], "candidate_id": c["candidate_id"], "verdict_good_or_bad": "", "notes": ""})
    _write(out_dir / "manual_audit.md", "\n".join(audit) + "\n")
    _write(out_dir / "manual_audit.csv", _csv_text(audit_rows, ["rank", "candidate_id", "verdict_good_or_bad", "notes"]))
    return m
