"""Run the Stage 4 promo pipeline on a ZIP or folder; use run labels for independent runs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from llm_client import LLMClient, ProviderError, atomic_write, load_config
from run_stage3 import prepare_candidate, prepare_judgements
from screenplay import load_show
from stage4_logic import WEIGHT_KEYS, exact_self_scores, make_cross_windows, rank_diverse, score_candidate
from stage4_prompts import cross_prompt, episode_prompt, judge_prompt
from stage4_reports import write_outputs
from window_options import make_local_windows, resolve_local_choice


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def preflight(config: dict) -> None:
    """Catch a missing local server/model or missing cloud key before a full batch."""
    if config["provider"] == "ollama_local":
        url = config["base_url"].rstrip("/") + "/api/tags"
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(Request(url, method="GET"), timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError, ValueError, UnicodeDecodeError, TypeError, AttributeError) as exc:
            raise ProviderError(f"Cannot reach local Ollama ({type(exc).__name__}); open the Ollama app first") from None
        names = {item.get("name") for item in data.get("models", []) if isinstance(item, dict)}
        if config["model"] not in names:
            raise ProviderError(f"Local model {config['model']} is not installed; download it with ollama pull")
    elif not os.environ.get(config["api_key_env"]):
        raise ProviderError(f"Environment variable {config['api_key_env']} is not set")


def check_settings(config: dict) -> None:
    weights = config.get("scoring_weights")
    if not isinstance(weights, dict) or set(weights) != set(WEIGHT_KEYS):
        raise ProviderError("scoring_weights must contain exactly the ten keys in config.stage4.local.json")
    if any(type(v) not in (int, float) or v < 0 for v in weights.values()) or abs(sum(weights.values()) - 1) > 1e-6:
        raise ProviderError("scoring_weights must be nonnegative and sum to 1")
    for key in ("max_candidates_per_episode", "judge_batch_size", "cross_episode_batch_size", "max_cross_options", "max_consecutive_failures"):
        if type(config.get(key)) is not int or config[key] < 1:
            raise ProviderError(f"{key} must be a positive integer")
    threshold = config.get("dedup_iou_threshold")
    if type(threshold) not in (int, float) or not 0 < threshold <= 1:
        raise ProviderError("dedup_iou_threshold must be greater than 0 and at most 1")
    for key in ("same_episode_penalty", "same_five_episode_block_penalty", "same_character_penalty"):
        if type(config.get(key)) not in (int, float) or config[key] < 0:
            raise ProviderError(f"{key} must be nonnegative")


def _checkpoint(out_dir: Path, state: dict) -> None:
    atomic_write(out_dir / "checkpoint.json", (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def show_fingerprint(episodes: dict[int, dict]) -> str:
    data = [{"number": n, "logline": episodes[n]["episode_logline"], "continuity": episodes[n]["continuity_notes"],
             "events": [{k: e.get(k) for k in ("event_id", "type", "speaker", "start", "end", "text")} for e in episodes[n]["events"]]}
            for n in sorted(episodes)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class MeteredClient(LLMClient):
    """Read token counts from cached provider responses, separating new and cached calls."""
    def __init__(self, config):
        super().__init__(config)
        self.usage = {"logical_responses": 0, "with_provider_usage": 0, "missing_provider_usage": 0,
                      "input_tokens": 0, "output_tokens": 0, "new_input_tokens": 0, "new_output_tokens": 0}

    def complete(self, prompt: str) -> str:
        previous_attempts = self.http_attempts
        try:
            return super().complete(prompt)
        finally:
            url, body = self._request_data(prompt)
            cache = self._cache_path(url, body)
            if cache.is_file():
                self.usage["logical_responses"] += 1
                try:
                    data = json.loads(cache.read_text(encoding="utf-8"))
                    if self.config["provider"] == "ollama_local":
                        inp, out = data.get("prompt_eval_count"), data.get("eval_count")
                    elif self.config["provider"] == "gemini":
                        usage = data.get("usageMetadata", {})
                        inp, out = usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
                    else:
                        usage = data.get("usage", {})
                        inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if type(inp) is not int or type(out) is not int:
                        raise ValueError("provider did not report both token counts")
                    self.usage["with_provider_usage"] += 1
                    self.usage["input_tokens"] += inp
                    self.usage["output_tokens"] += out
                    if self.http_attempts > previous_attempts:
                        self.usage["new_input_tokens"] += inp
                        self.usage["new_output_tokens"] += out
                except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
                    self.usage["missing_provider_usage"] += 1


def _consume(response: dict, windows: list[dict], state: dict, episodes: dict[int, dict],
             config: dict, origin: str, limit: int, expected_episode: int | None = None, cross: bool = False) -> int:
    accepted, used = 0, set()
    for index, raw in enumerate(response["candidates"], 1):
        label = f"{origin}-C{index:02d}"
        if index > limit:
            state["rejections"].append({"candidate_id": label, "stage": origin, "errors": ["exceeded proposal limit"]})
            continue
        window_id = raw.get("window_id") if isinstance(raw, dict) else None
        if isinstance(window_id, str) and window_id in used:
            state["rejections"].append({"candidate_id": label, "stage": origin, "errors": ["duplicate window ID in response"]})
            continue
        resolved, error = resolve_local_choice(raw, windows)
        if error:
            state["rejections"].append({"candidate_id": label, "stage": origin, "errors": [error]})
            continue
        used.add(window_id)
        candidate, errors, corrections = prepare_candidate(resolved, label, episodes, config, expected_episode, cross)
        state["timestamp_corrections"].extend(corrections)
        if errors:
            state["rejections"].append({"candidate_id": label, "stage": origin, "errors": errors})
            continue
        candidate["source_window_id"] = window_id
        candidate["self_scores"] = exact_self_scores(raw.get("self_scores"))
        state["candidates"].append(candidate)
        accepted += 1
    return accepted


def _judge_response(response: dict, batch: list[dict], episodes: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """Validate model IDs and scores; fill hook text only after checking its source event ID."""
    known = {c["candidate_id"]: c for c in batch}
    translated = []
    for item in response.get("judgements", []):
        if not isinstance(item, dict):
            translated.append(item)
            continue
        item = dict(item)
        candidate = known.get(item.get("candidate_id")) if isinstance(item.get("candidate_id"), str) else None
        if candidate and item.get("hook_event_id") == candidate["hook_event_id"]:
            item["exact_hook_quote"] = candidate["hook"]["quote"]
        elif candidate:
            item["exact_hook_quote"] = ""
        translated.append(item)
    accepted, rejected = prepare_judgements({"judgements": translated}, batch, episodes)
    for result in accepted:
        result["hook_attribution"] = "Source text inserted by code after matching the judge's hook event ID."
    return accepted, rejected


def execute(input_path: str, config: dict, run_label: str, *, client=None, do_preflight: bool = True,
            progress=None) -> tuple[dict, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", run_label):
        raise ValueError("run-label must contain 1–24 letters, digits, underscores or hyphens")
    check_settings(config)
    episodes, warnings = load_show(input_path)
    if not episodes:
        raise ValueError("No screenplay episodes were parsed from the input")
    if do_preflight:
        preflight(config)
    cfg = copy.deepcopy(config)
    out_dir = Path(config["raw_outputs_dir"]) / f"run_{run_label}"
    cfg["raw_outputs_dir"] = str(out_dir)
    client = client or MeteredClient(cfg)
    def notify(phase: str, done: int, total: int, message: str) -> None:
        if progress:
            try:
                progress({"phase": phase, "done": done, "total": total, "message": message})
            except Exception:
                # Display failures must not stop the underlying batch or lose cache.
                pass
    state = {"status": "running", "run_label": run_label, "provider": cfg["provider"], "model": cfg["model"],
             "show_fingerprint": show_fingerprint(episodes),
             "generation_settings": {k: cfg.get(k) for k in ("provider", "model", "base_url", "temperature", "max_output_tokens",
                                       "context_tokens", "use_json_mode", "max_candidates_per_episode", "judge_batch_size",
                                       "cross_episode_batch_size", "max_cross_options", "scoring_weights", "dedup_iou_threshold",
                                       "same_episode_penalty", "same_five_episode_block_penalty", "same_character_penalty")},
             "episodes": sorted(episodes), "parser_warnings": warnings, "episode_passes": [], "cross_passes": [],
             "candidates": [], "judgements": [], "rejections": [], "errors": [], "json_retries": [],
             "timestamp_corrections": [], "http_attempts": 0, "cache_hits": 0}
    out_dir.mkdir(parents=True, exist_ok=True)
    consecutive_failures = 0
    interrupted = False
    try:
        notify("episodes", 0, len(episodes), "Starting episode analysis")
        for position, number in enumerate(sorted(episodes), 1):
            print(f"Episode {number} ({position}/{len(episodes)}): selecting promos...", flush=True)
            earlier = [n for n in sorted(episodes) if n < number][-2:]
            previous = [{"episode": n, "logline": episodes[n]["episode_logline"]} for n in earlier]
            windows = make_local_windows(episodes[number])
            if not windows:
                state["episode_passes"].append({"episode": number, "proposed": 0, "validated": 0, "status": "no_windows"})
                state["errors"].append(f"episode {number}: no structurally valid windows")
                _checkpoint(out_dir, state)
                notify("episodes", position, len(episodes), f"Episode {number}: no valid windows")
                continue
            prompt = episode_prompt(episodes[number], previous, windows, cfg["max_candidates_per_episode"], run_label)
            try:
                response, retries = client.ask_json(prompt, "candidates")
                state["json_retries"].extend(f"episode {number}: {item}" for item in retries)
                if response is None:
                    state["errors"].append(f"episode {number}: invalid JSON after retry")
                    state["episode_passes"].append({"episode": number, "proposed": 0, "validated": 0, "status": "invalid_json"})
                else:
                    accepted = _consume(response, windows, state, episodes, cfg, f"EP{number:03d}",
                                        cfg["max_candidates_per_episode"], expected_episode=number)
                    state["episode_passes"].append({"episode": number, "proposed": len(response["candidates"]),
                                                   "validated": accepted, "offered_windows": len(windows), "status": "processed"})
                consecutive_failures = 0
            except ProviderError as exc:
                consecutive_failures += 1
                state["errors"].append(f"episode {number}: {exc}")
                state["episode_passes"].append({"episode": number, "proposed": 0, "validated": 0, "status": "provider_error"})
            _checkpoint(out_dir, state)
            notify("episodes", position, len(episodes), f"Processed episode {number}")
            if consecutive_failures >= cfg["max_consecutive_failures"]:
                state["errors"].append("Stopped after consecutive provider failures; rerun the same label to reuse cached calls")
                break

        # A partial episode run is never presented as a complete 50-episode run.
        if len(state["episode_passes"]) == len(episodes) and consecutive_failures < cfg["max_consecutive_failures"]:
            nums = sorted(episodes)
            size = cfg["cross_episode_batch_size"]
            total_batches = (len(nums) + size - 1) // size
            for offset in range(0, len(nums), size):
                batch_numbers = nums[offset:offset + size]
                options = make_cross_windows(episodes, state["candidates"], batch_numbers, cfg["max_cross_options"])
                origin = f"CROSS{offset // size + 1:02d}"
                if not options:
                    state["cross_passes"].append({"batch": batch_numbers, "proposed": 0, "validated": 0, "status": "no_options"})
                    notify("cross", offset // size + 1, total_batches, "Skipped a cross-episode batch")
                    continue
                print(f"Cross-episode batch {offset // size + 1}: selecting combinations...", flush=True)
                try:
                    response, retries = client.ask_json(cross_prompt(options, batch_numbers, episodes, run_label), "candidates")
                    state["json_retries"].extend(f"{origin}: {item}" for item in retries)
                    if response is None:
                        state["cross_passes"].append({"batch": batch_numbers, "proposed": 0, "validated": 0, "status": "invalid_json"})
                        state["errors"].append(f"{origin}: invalid JSON after retry")
                    else:
                        accepted = _consume(response, options, state, episodes, cfg, origin, 3, cross=True)
                        state["cross_passes"].append({"batch": batch_numbers, "proposed": len(response["candidates"]),
                                                     "validated": accepted, "offered_windows": len(options), "status": "processed"})
                except ProviderError as exc:
                    state["cross_passes"].append({"batch": batch_numbers, "proposed": 0, "validated": 0, "status": "provider_error"})
                    state["errors"].append(f"{origin}: {exc}")
                _checkpoint(out_dir, state)
                notify("cross", offset // size + 1, total_batches, "Processed a cross-episode batch")

        size = cfg["judge_batch_size"]
        judge_failures = 0
        for offset in range(0, len(state["candidates"]), size):
            batch = state["candidates"][offset:offset + size]
            print(f"Judging promos {offset + 1}–{offset + len(batch)} of {len(state['candidates'])}...", flush=True)
            try:
                response, retries = client.ask_json(judge_prompt(batch, episodes, run_label), "judgements")
                state["json_retries"].extend(f"judge batch {offset // size + 1}: {item}" for item in retries)
                if response is None:
                    state["errors"].append(f"judge batch {offset // size + 1}: invalid JSON after retry")
                else:
                    accepted, rejected = _judge_response(response, batch, episodes)
                    state["judgements"].extend(accepted)
                    state["rejections"].extend({**item, "stage": "judge"} for item in rejected)
                judge_failures = 0
            except ProviderError as exc:
                judge_failures += 1
                state["errors"].append(f"judge batch {offset // size + 1}: {exc}")
            _checkpoint(out_dir, state)
            notify("judge", min(offset + len(batch), len(state["candidates"])), len(state["candidates"]), "Reviewed a candidate batch")
            if judge_failures >= cfg["max_consecutive_failures"]:
                state["errors"].append("Stopped judging after consecutive provider failures; rerun the same label")
                break
    except KeyboardInterrupt:
        interrupted = True
        state["errors"].append("Interrupted by user; rerun the same label to reuse cached calls")
    finally:
        state["http_attempts"] = client.http_attempts
        state["cache_hits"] = client.cache_hits
        state["usage"] = getattr(client, "usage", None)
        all_processed = len(state["episode_passes"]) == len(episodes) and all(p["status"] == "processed" for p in state["episode_passes"])
        all_judged = len(state["judgements"]) == len(state["candidates"])
        state["status"] = "completed" if all_processed and all_judged and state["candidates"] and not state["errors"] and not interrupted else "partial"
        jmap = {j["candidate_id"]: j for j in state["judgements"]}
        scored, unscored = [], []
        for candidate in state["candidates"]:
            judge = jmap.get(candidate["candidate_id"])
            if judge is None:
                unscored.append(candidate)
            else:
                scored.append({**candidate, "judge": judge, **score_candidate(candidate, judge, episodes, cfg["scoring_weights"])})
        ranked, removed = rank_diverse(scored, episodes, cfg["dedup_iou_threshold"], cfg["same_episode_penalty"],
                                       cfg["same_five_episode_block_penalty"], cfg["same_character_penalty"])
        state["metrics"] = write_outputs(out_dir, state, episodes, ranked, unscored, removed)
        _checkpoint(out_dir, state)
        notify("done", 1, 1, f"Run {state['status']}")
    return state, out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="ZIP or folder of screenplay files")
    parser.add_argument("--config", default="config.stage4.local.json", help="Model, throttling and scoring settings")
    parser.add_argument("--run-label", default="A", help="Use distinct labels A and B for independent evaluations")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        state, out_dir = execute(args.input, config, args.run_label)
    except (ValueError, OSError, ProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    processed = sum(p["status"] == "processed" for p in state["episode_passes"])
    print(f"Run {args.run_label}: {state['status']}; processed {processed}/{len(state['episodes'])} episodes; "
          f"validated {len(state['candidates'])}; judged {len(state['judgements'])}.")
    print(f"Private results: {out_dir}; check eval_report.md and manual_audit.md.")
    print("Model scores are not independent human ratings. Review scripts and video before publishing.")
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
