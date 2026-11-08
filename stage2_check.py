"""Run parsing and source-backed validator smoke checks on a screenplay pack."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from screenplay import MICROSECONDS, load_show, show_counts
from validators import cut_points, validate_candidate


def _sample_candidate(episode: dict, points: list[dict]) -> dict | None:
    """Build a source-backed structural test case, not an editorial promo pick."""
    safe = [point for point in points if point["safe"]]
    for event in episode["events"]:
        if event["type"] != "dialogue":
            continue
        possible_starts = [point for point in safe if (
            point["time_us"] <= event["start_us"] < point["time_us"] + 3 * MICROSECONDS
        )]
        possible_starts.sort(key=lambda point: event["start_us"] - point["time_us"])
        for start in possible_starts:
            possible_ends = [point for point in safe if (
                start["time_us"] + 20 * MICROSECONDS <= point["time_us"]
                <= start["time_us"] + 60 * MICROSECONDS
                and point["time_us"] >= event["end_us"]
            )]
            possible_ends.sort(key=lambda point: abs(point["time_us"] - start["time_us"] - 40 * MICROSECONDS))
            if not possible_ends:
                continue
            end = possible_ends[0]
            return {
                "segments": [{
                    "episode": episode["episode_number"],
                    "start": start["source_timestamps"][0],
                    "end": end["source_timestamps"][0],
                }],
                "hook": {
                    "episode": episode["episode_number"],
                    "timestamp": event["start"],
                    "quote": event["text"],
                },
                "cliffhanger": "Editorial review pending.",
                "why_it_works": "Editorial review pending.",
                "context_needed": "Editorial review pending.",
            }
    return None


def check_show(episodes: dict[int, dict], parse_warnings: list[str]) -> dict:
    per_episode = []
    totals = {
        "source_boundaries": 0,
        "safe_boundaries": 0,
        "boundaries_inside_dialogue": 0,
        "boundaries_splitting_ellipsis": 0,
        "episodes_with_structural_sample": 0,
        "valid_structural_samples": 0,
        "invented_timestamp_rejections": 0,
        "invented_hook_rejections": 0,
        "mid_dialogue_rejections": 0,
    }
    failures: list[str] = []
    for number, episode in episodes.items():
        points = cut_points(episode)
        dialogue_unsafe = sum(any("inside dialogue" in reason for reason in point["reasons"]) for point in points)
        continuation_unsafe = sum(any("ellipsis continuation" in reason for reason in point["reasons"]) for point in points)
        totals["source_boundaries"] += len(points)
        totals["safe_boundaries"] += sum(point["safe"] for point in points)
        totals["boundaries_inside_dialogue"] += dialogue_unsafe
        totals["boundaries_splitting_ellipsis"] += continuation_unsafe
        candidate = _sample_candidate(episode, points)
        sample_status = "no suitable structural sample"
        if candidate:
            totals["episodes_with_structural_sample"] += 1
            result = validate_candidate(candidate, episodes)
            if result["valid"]:
                totals["valid_structural_samples"] += 1
                sample_status = "passed"
            else:
                sample_status = "failed"
                failures.append(f"episode {number}: source-backed sample failed: {result['errors']}")

            invented_time = copy.deepcopy(candidate)
            invented_time["segments"][0]["end"] = "999:59.99"
            time_check = validate_candidate(invented_time, episodes)
            if not time_check["valid"] and any("not an exact source event boundary" in error for error in time_check["errors"]):
                totals["invented_timestamp_rejections"] += 1
            else:
                failures.append(f"episode {number}: invented timestamp was not rejected for the expected reason")

            invented_hook = copy.deepcopy(candidate)
            invented_hook["hook"]["quote"] = "Invented line that does not occur in the screenplay."
            hook_check = validate_candidate(invented_hook, episodes)
            if not hook_check["valid"] and any("do not match" in error for error in hook_check["errors"]):
                totals["invented_hook_rejections"] += 1
            else:
                failures.append(f"episode {number}: invented hook was not rejected for the expected reason")

            start_us = next(point["time_us"] for point in points if candidate["segments"][0]["start"] in point["source_timestamps"])
            unsafe_ends = [point for point in points if (
                start_us + 20 * MICROSECONDS <= point["time_us"] <= start_us + 60 * MICROSECONDS
                and any("inside dialogue" in reason for reason in point["reasons"])
            )]
            if unsafe_ends:
                midline = copy.deepcopy(candidate)
                midline["segments"][0]["end"] = unsafe_ends[0]["source_timestamps"][0]
                midline_check = validate_candidate(midline, episodes)
                if not midline_check["valid"] and any("inside dialogue" in error for error in midline_check["errors"]):
                    totals["mid_dialogue_rejections"] += 1
                else:
                    failures.append(f"episode {number}: mid-dialogue source boundary was not rejected")
        per_episode.append({
            "episode": number,
            "scenes": len(episode["scenes"]),
            "events": len(episode["events"]),
            "source_boundaries": len(points),
            "safe_boundaries": sum(point["safe"] for point in points),
            "structural_sample": sample_status,
        })
    return {
        "summary": show_counts(episodes),
        "checks": totals,
        "parser_warning_count": len(parse_warnings),
        "parser_warnings": parse_warnings,
        "test_failures": failures,
        "per_episode": per_episode,
        "note": "Structural samples are validator tests, not AI-selected or editorially judged promos.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Folder or ZIP containing screenplay .txt files")
    parser.add_argument("--output", default="stage2_output", help="Report directory (default: stage2_output)")
    args = parser.parse_args()
    try:
        episodes, warnings = load_show(args.input)
        report = check_show(episodes, warnings)
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / "stage2_results.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    summary, checks = report["summary"], report["checks"]
    print(f"Parsed {summary['episodes']} episodes, {summary['scenes']} scenes, {summary['events']} events.")
    print(f"Parser warnings: {report['parser_warning_count']}")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    print(f"Safe source boundaries: {checks['safe_boundaries']} / {checks['source_boundaries']}")
    print(f"Source-backed structural samples passed: {checks['valid_structural_samples']} / {checks['episodes_with_structural_sample']}")
    print(f"Invented timestamp rejections: {checks['invented_timestamp_rejections']}")
    print(f"Invented hook rejections: {checks['invented_hook_rejections']}")
    print(f"Mid-dialogue cut rejections: {checks['mid_dialogue_rejections']}")
    print(f"Failures: {len(report['test_failures'])}")
    print(f"Report: {report_path}")
    print(report["note"])
    return 1 if not episodes or not summary["events"] or report["test_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
