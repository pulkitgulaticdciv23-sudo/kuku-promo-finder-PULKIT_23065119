"""Compare exact top-10 source segments from two independently labelled runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from llm_client import atomic_write
from stage4_logic import stable_key


def compare(left: dict, right: dict) -> dict:
    if left.get("show_fingerprint") != right.get("show_fingerprint") or not left.get("show_fingerprint"):
        raise ValueError("Runs must use the exact same parsed show")
    if str(left.get("run_label", "")).casefold() == str(right.get("run_label", "")).casefold():
        raise ValueError("Use two distinct run labels; comparing a run to itself is not an independent check")
    if left.get("generation_settings") != right.get("generation_settings"):
        raise ValueError("Runs must use the same provider, model and generation settings")
    a = {stable_key(c) for c in left.get("ranked", [])[:10]}
    b = {stable_key(c) for c in right.get("ranked", [])[:10]}
    intersection, union = len(a & b), len(a | b)
    return {"run_a": left["run_label"], "run_b": right["run_label"], "status_a": left["status"], "status_b": right["status"],
            "top_a_count": len(a), "top_b_count": len(b), "exact_matches": intersection,
            "jaccard_percent": round(100 * intersection / union, 1) if union else None,
            "meaning": "Exact episode/start/end segment identity; similar but different cuts do not count as matches."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, help="Path to run A's promos.json")
    parser.add_argument("--b", required=True, help="Path to run B's promos.json")
    parser.add_argument("--output", default="raw_outputs/stability_report.md")
    args = parser.parse_args()
    try:
        left = json.loads(Path(args.a).read_text(encoding="utf-8"))
        right = json.loads(Path(args.b).read_text(encoding="utf-8"))
        result = compare(left, right)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    lines = ["# Run-to-run top-10 stability", "", f"Runs: {result['run_a']} ({result['status_a']}) and {result['run_b']} ({result['status_b']}).",
             f"Top lists: {result['top_a_count']} and {result['top_b_count']} promos.",
             f"Exact shared promos: {result['exact_matches']}; Jaccard overlap: {result['jaccard_percent'] if result['jaccard_percent'] is not None else 'N/A'}%.",
             result["meaning"], "Distinct labels change the prompts and cache keys; they do not guarantee statistically independent samples.",
             "Partial runs or lists with fewer than ten ranked promos limit interpretation.", ""]
    atomic_write(Path(args.output), "\n".join(lines).encode("utf-8"))
    summary = (f"Compared run {result['run_a']} ({result['status_a']}) and {result['run_b']} ({result['status_b']}): "
               f"{result['exact_matches']} exact shared promos from lists of {result['top_a_count']} and {result['top_b_count']}; "
               f"Jaccard {result['jaccard_percent'] if result['jaccard_percent'] is not None else 'N/A'}%. "
               "Exact episode/start/end matches only; partial or short lists limit interpretation.")
    for report_path in (Path(args.a).parent / "eval_report.md", Path(args.b).parent / "eval_report.md"):
        if report_path.is_file():
            report = report_path.read_text(encoding="utf-8")
            start = report.find("## Run-to-run stability\n\n")
            end = report.find("\n## Detected issues from this run", start)
            if start >= 0 and end > start:
                prefix = "## Run-to-run stability\n\n"
                report = report[:start] + prefix + summary + "\n" + report[end:]
                atomic_write(report_path, report.encode("utf-8"))
    print("\n".join(lines))
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
