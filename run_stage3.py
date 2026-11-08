"""Generate and judge candidates from at most three selected episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from llm_client import LLMClient, ProviderError, atomic_write, load_config
from prompts import cross_episode_prompt, episode_prompt, judge_prompt, local_window_prompt
from screenplay import load_show, time_to_us
from validators import snap_timestamp, validate_candidate
from window_options import make_local_windows, resolve_local_choice


JUDGE_KEYS = (
    "hook_strength", "cliffhanger_strength", "standalone_clarity",
    "emotional_intensity", "spoiler_risk", "genre_fit",
)


def _same_text(left: str, right: str) -> bool:
    def without_outer_quotes(value):
        value = value.strip()
        for opening, closing in (("\"", "\""), ("“", "”")):
            if len(value) >= 2 and value.startswith(opening) and value.endswith(closing):
                return value[1:-1]
        return value
    return without_outer_quotes(left) == without_outer_quotes(right)


def prepare_candidate(raw: object, label: str, episodes: dict[int, dict], config: dict,
                      expected_episode: int | None = None, require_cross: bool = False) -> tuple[dict | None, list[str], list[dict]]:
    """Resolve the hook from source, snap near boundaries, and reject unsafe output."""
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        return None, ["model candidate must contain a segments list"], []
    corrections = []
    segments = []
    for index, segment in enumerate(raw["segments"], 1):
        if not isinstance(segment, dict):
            return None, [f"segment {index} is not an object"], corrections
        number = segment.get("episode")
        if type(number) is not int or number not in episodes:
            return None, [f"segment {index} refers to an unselected or unknown episode"], corrections
        if expected_episode is not None and number != expected_episode:
            return None, [f"episode pass selected an unexpected episode in segment {index}"], corrections
        corrected = {"episode": number}
        for field in ("start", "end"):
            original = segment.get(field)
            try:
                snapped = snap_timestamp(episodes[number], original, max_shift_seconds=config.get("snap_tolerance_seconds", 0.25))
            except (ValueError, TypeError) as exc:
                return None, [f"segment {index} {field} cannot be snapped safely: {exc}"], corrections
            corrected[field] = snapped
            if original != snapped:
                corrections.append({"candidate_id": label, "segment": index, "field": field, "model_timestamp": original, "source_timestamp": snapped})
        segments.append(corrected)
    if require_cross and len({item["episode"] for item in segments}) < 2:
        return None, ["cross-episode candidate uses fewer than two episodes"], corrections
    hook_id = raw.get("hook_event_id")
    hook_event = next((event for ep in episodes.values() for event in ep["events"] if event["event_id"] == hook_id), None)
    if hook_event is None:
        return None, ["hook_event_id does not exist in the selected source episodes"], corrections
    hook_episode = next(num for num, ep in episodes.items() if hook_event in ep["events"])
    candidate = {
        "candidate_id": label,
        "segments": segments,
        "hook": {"episode": hook_episode, "timestamp": hook_event["start"], "quote": hook_event["text"]},
        "hook_event_id": hook_id,
        "cliffhanger": raw.get("cliffhanger"),
        "why_it_works": raw.get("why_it_works"),
        "context_needed": raw.get("context_needed"),
    }
    result = validate_candidate(candidate, episodes)
    if not result["valid"]:
        return None, result["errors"], corrections
    candidate["duration_seconds"] = result["duration_seconds"]
    candidate["validation_warnings"] = result["warnings"]
    return candidate, [], corrections


def prepare_judgements(response: dict, candidates: list[dict], episodes: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """Reject scores, explanations, or hook quotes that fail basic validation."""
    known = {item["candidate_id"]: item for item in candidates}
    accepted, rejected, seen = [], [], set()
    for index, item in enumerate(response.get("judgements", []), 1):
        if not isinstance(item, dict):
            rejected.append({"index": index, "errors": ["judgement is not an object"]})
            continue
        cid = item.get("candidate_id")
        if not isinstance(cid, str) or cid not in known or cid in seen:
            rejected.append({"index": index, "errors": ["unknown or duplicate candidate ID"]})
            continue
        seen.add(cid)
        errors = []
        quote = item.get("exact_hook_quote")
        if not isinstance(quote, str) or not _same_text(quote, known[cid]["hook"]["quote"]):
            errors.append("judge hook quote does not match the exact source hook")
        scores = item.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores must be an object")
        else:
            for key in JUDGE_KEYS:
                score = scores.get(key)
                if not isinstance(score, dict) or type(score.get("score")) is not int or not 1 <= score["score"] <= 10:
                    errors.append(f"{key} must contain an integer score from 1 to 10")
                elif not isinstance(score.get("justification"), str) or not score["justification"].strip():
                    errors.append(f"{key} must contain a nonempty justification")
                elif "\n" in score["justification"] or "\r" in score["justification"]:
                    errors.append(f"{key} justification must be one line")
        if not errors:
            judgement = {"candidate_id": cid, "exact_hook_quote": known[cid]["hook"]["quote"], "scores": {key: scores[key] for key in JUDGE_KEYS}}
            combined = dict(known[cid])
            combined["judge"] = judgement
            verdict = validate_candidate(combined, episodes)
            if not verdict["valid"]:
                errors.extend(verdict["errors"])
        if errors:
            rejected.append({"candidate_id": cid, "errors": errors})
        else:
            accepted.append(judgement)
    for cid in known.keys() - seen:
        rejected.append({"candidate_id": cid, "errors": ["judge omitted candidate"]})
    return accepted, rejected


def _append_detected_errors(out_dir: Path, report: dict) -> None:
    """Keep a deduplicated private log of errors actually caught in model output."""
    incidents = {
        "invalid_json_attempts": report["json_retries"],
        "rejected_candidates_or_judgements": report["rejections"],
        "timestamp_corrections": report["timestamp_corrections"],
    }
    if not any(incidents.values()):
        return
    serialized = json.dumps(incidents, sort_keys=True, ensure_ascii=False, indent=2)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    path = out_dir / "WHERE_AI_GOT_IT_WRONG.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Detected model-output errors\n\nPrivate: this may contain source timestamps.\n"
    marker = f"<!-- incident {digest} -->"
    if marker in existing:
        return
    entry = (
        f"\n## Detected {datetime.now(timezone.utc).isoformat()}\n\n{marker}\n\n"
        "The following are detected errors or corrections, not approved promo claims. "
        "See the raw request/response files for original model output.\n\n"
        f"```json\n{serialized}\n```\n"
    )
    atomic_write(path, (existing + entry).encode("utf-8"))


def _write_report(out_dir: Path, report: dict) -> None:
    atomic_write(out_dir / "stage3_results.json", (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    lines = [
        "# Stage 3 results", "", f"Status: {report['status']}",
        f"Selected episodes: {', '.join(map(str, report['selected_episodes']))}",
        f"Provider / model: {report['provider']} / {report['model']}",
        f"Validated candidates: {len(report['candidates'])}",
        f"Validated judge responses: {len(report['judgements'])}",
        f"Rejected candidate or judgement entries: {len(report['rejections'])}",
        f"Model timestamp corrections: {len(report['timestamp_corrections'])}",
        f"HTTP attempts: {report['http_attempts']}; cache hits: {report['cache_hits']}",
        f"Cross-episode pass: {report.get('cross_episode_status', 'not recorded')}",
        "", "Status reports execution, not editorial quality. Scores require human review.",
        "The same configured model proposes and judges; its scores are not independent.",
        "A high spoiler-risk score means higher risk.",
        "Only the selected episodes were shown to the judge; later twists are not checked.", "",
    ]
    if report["errors"]:
        lines.extend(["## Errors", ""] + [f"- {item}" for item in report["errors"]] + [""])
    if report["episode_passes"]:
        lines.extend(["## Episode passes", "", "| Episode | Model proposals | Passed validation | Status |", "| --- | ---: | ---: | --- |"])
        for entry in report["episode_passes"]:
            lines.append(f"| {entry['episode']} | {entry['proposed']} | {entry['validated']} | {entry['status']} |")
        lines.append("")
    judgements = {item["candidate_id"]: item for item in report["judgements"]}
    for candidate in report["candidates"]:
        lines.extend([
            f"## {candidate['candidate_id']}",
            "", f"Segments: {json.dumps(candidate['segments'], ensure_ascii=False)}",
            f"Duration: {candidate['duration_seconds']:.2f}s",
            f"Source hook: {candidate['hook']['quote']}",
            f"Cliffhanger: {candidate['cliffhanger']}",
            f"Why: {candidate['why_it_works']}",
            f"Context: {candidate['context_needed']}", "",
        ])
        if candidate["candidate_id"] in judgements:
            lines.extend(["| Judge dimension | Score / 10 | Reason |", "| --- | ---: | --- |"])
            for name, score in judgements[candidate["candidate_id"]]["scores"].items():
                reason = score["justification"].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {name} | {score['score']} | {reason} |")
            lines.append("")
    atomic_write(out_dir / "stage3_report.md", ("\n".join(lines) + "\n").encode("utf-8"))
    _append_detected_errors(out_dir, report)


def run(input_path: str, selected: list[int], config: dict, *, client: LLMClient | None = None) -> dict:
    if not 1 <= len(selected) <= 3 or len(selected) != len(set(selected)):
        raise ValueError("Stage 3 accepts one to three distinct episode numbers")
    all_episodes, parser_warnings = load_show(input_path)
    if any(number not in all_episodes for number in selected):
        raise ValueError("One or more selected episodes are not present in the input")
    selected = sorted(selected)
    episodes = {number: all_episodes[number] for number in selected}
    client = client or LLMClient(config)
    report = {
        "status": "running", "selected_episodes": selected,
        "provider": config["provider"], "model": config["model"],
        "parser_warnings": parser_warnings, "candidates": [], "judgements": [],
        "rejections": [], "timestamp_corrections": [], "errors": [], "json_retries": [],
        "episode_passes": [],
        "cross_episode_status": "not_started",
        "quality_note": "Execution success does not establish promo quality; the same model proposes and judges.",
        "http_attempts": 0, "cache_hits": 0,
    }
    out_dir = Path(config["raw_outputs_dir"])

    def consume(response: dict, origin: str, expected: int | None = None, cross: bool = False, limit: int = 5,
                local_windows: list[dict] | None = None):
        proposals = response["candidates"]
        existing_windows = [segment for item in report["candidates"] for segment in item["segments"]]
        used_ids = set()
        if len(proposals) > limit:
            report["errors"].append(f"{origin}: model returned more than {limit} proposals; extras were ignored")
        for index, raw in enumerate(proposals[:limit], 1):
            label = f"{origin}-C{index:02d}"
            chosen_id = None
            if local_windows is not None:
                chosen_id = raw.get("window_id") if isinstance(raw, dict) else None
                if isinstance(chosen_id, str) and chosen_id in used_ids:
                    report["rejections"].append({"candidate_id": label, "errors": ["duplicate selected window_id"]})
                    continue
                # A malformed ID is rejected without inventing a replacement.
                resolved, resolution_error = resolve_local_choice(raw, local_windows)
                if resolution_error:
                    report["rejections"].append({"candidate_id": label, "errors": [resolution_error]})
                    continue
                used_ids.add(chosen_id)
                raw = resolved
            candidate, errors, corrections = prepare_candidate(raw, label, episodes, config, expected, cross)
            report["timestamp_corrections"].extend(corrections)
            if candidate and cross and config["provider"] == "ollama_local":
                for segment in candidate["segments"]:
                    if not any(
                        segment["episode"] == window["episode"]
                        and time_to_us(window["start"]) <= time_to_us(segment["start"])
                        and time_to_us(segment["end"]) <= time_to_us(window["end"])
                        for window in existing_windows
                    ):
                        errors.append("cross-episode segment lies outside the source windows shown to the model")
                        candidate = None
                        break
            if candidate:
                if chosen_id:
                    candidate["source_window_id"] = chosen_id
                report["candidates"].append(candidate)
            else:
                report["rejections"].append({"candidate_id": label, "errors": errors})

    try:
        for number in selected:
            previous = [{"episode": earlier, "logline": all_episodes[earlier]["episode_logline"]}
                        for earlier in sorted(all_episodes) if earlier < number][-2:]
            print(f"Generating candidates for episode {number}...", flush=True)
            max_candidates = config.get("max_candidates_per_episode", 5)
            local_windows = make_local_windows(episodes[number]) if config["provider"] == "ollama_local" else None
            if local_windows is not None and not local_windows:
                report["errors"].append(f"episode {number}: no valid precomputed windows")
                report["episode_passes"].append({"episode": number, "proposed": 0, "validated": 0, "status": "no_windows"})
                continue
            prompt = (local_window_prompt(episodes[number], previous, local_windows, max_candidates)
                      if local_windows is not None else episode_prompt(episodes[number], previous, max_candidates))
            response, retries = client.ask_json(prompt, "candidates")
            report["json_retries"].extend(f"episode {number}: {item}" for item in retries)
            if response is None:
                report["errors"].append(f"episode {number}: invalid JSON after one retry")
                report["episode_passes"].append({"episode": number, "proposed": 0, "validated": 0, "status": "invalid_json"})
                continue
            before = len(report["candidates"])
            consume(response, f"EP{number:03d}", expected=number, limit=max_candidates, local_windows=local_windows)
            report["episode_passes"].append({"episode": number, "proposed": len(response["candidates"]),
                                             "validated": len(report["candidates"]) - before, "status": "processed"})
        available_episodes = {segment["episode"] for item in report["candidates"] for segment in item["segments"]}
        can_cross = bool(report["candidates"]) and len(selected) >= 2
        if config["provider"] == "ollama_local" and len(available_episodes) < 2:
            can_cross = False
            report["cross_episode_status"] = "skipped: fewer than two episodes have validated source windows"
        elif not can_cross:
            report["cross_episode_status"] = "skipped: insufficient episodes or candidates"
        if can_cross:
            print("Generating optional cross-episode candidates...", flush=True)
            report["cross_episode_status"] = "attempted"
            compact = config["provider"] == "ollama_local"
            response, retries = client.ask_json(cross_episode_prompt(list(episodes.values()), report["candidates"], compact=compact), "candidates")
            report["json_retries"].extend(f"cross episode: {item}" for item in retries)
            if response is None:
                report["errors"].append("cross episode: invalid JSON after one retry")
            else:
                consume(response, "CROSS", cross=True, limit=3)
        if report["candidates"]:
            batch_size = config.get("judge_batch_size", len(report["candidates"]))
            for first in range(0, len(report["candidates"]), batch_size):
                batch = report["candidates"][first:first + batch_size]
                print(f"Judging candidates {first + 1}-{first + len(batch)}...", flush=True)
                response, retries = client.ask_json(judge_prompt(batch, episodes), "judgements")
                report["json_retries"].extend(f"judge: {item}" for item in retries)
                if response is None:
                    report["errors"].append(f"judge batch {first + 1}: invalid JSON after one retry")
                else:
                    accepted, rejected = prepare_judgements(response, batch, episodes)
                    report["judgements"].extend(accepted)
                    report["rejections"].extend(rejected)
    except ProviderError as exc:
        report["errors"].append(str(exc))
    finally:
        report["http_attempts"] = client.http_attempts
        report["cache_hits"] = client.cache_hits
        complete_judging = len(report["judgements"]) == len(report["candidates"])
        report["status"] = "completed" if report["candidates"] and complete_judging and not report["errors"] else "partial"
        _write_report(out_dir, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Folder or ZIP with screenplay files")
    parser.add_argument("--episodes", default="1,2,3", help="One to three episode numbers, comma-separated")
    parser.add_argument("--config", default="config.json", help="Model and rate configuration JSON")
    args = parser.parse_args()
    try:
        selected = [int(value.strip()) for value in args.episodes.split(",") if value.strip()]
        config = load_config(args.config)
        report = run(args.input, selected, config)
    except (ValueError, OSError, ProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Stage 3 status: {report['status']}; valid candidates: {len(report['candidates'])}; judged: {len(report['judgements'])}.")
    print("Status reflects execution, not promo quality. Model scores require human review.")
    print(f"Cross-episode pass: {report['cross_episode_status']}.")
    print(f"Rejected entries: {len(report['rejections'])}; timestamp corrections: {len(report['timestamp_corrections'])}.")
    for error in report["errors"]:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"Private output: {Path(config['raw_outputs_dir']) / 'stage3_results.json'}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
