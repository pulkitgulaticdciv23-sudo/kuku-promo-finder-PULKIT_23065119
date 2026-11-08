"""Tests of ranking, grounding, cross-episode options and resumable execution."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from compare_runs import compare
from fixtures import screenplay_text
from llm_client import ProviderError, load_config
from run_stage4 import MeteredClient, _judge_response, execute
from screenplay import load_show
from stage4_logic import SCORE_KEYS, make_cross_windows, overlap_iou, score_candidate
from validators import validate_candidate
from window_options import make_local_windows


def result_for(prompt, required):
    if required == "judgements":
        material = json.loads(prompt.split("\nCANDIDATE MATERIAL:\n", 1)[1])
        return {"judgements": [{"candidate_id": c["candidate_id"], "hook_event_id": c["hook_event_id"],
                                "scores": {k: {"score": 5, "justification": "A fictional test assessment."}
                                           for k in SCORE_KEYS}} for c in material]}
    if "\nEPISODE DATA:\n" in prompt:
        data = json.loads(prompt.split("\nEPISODE DATA:\n", 1)[1])
        options = data["candidate_windows"][:2]
    else:
        options = json.loads(prompt.split("\nCROSS OPTIONS:\n", 1)[1])[:1]
    return {"candidates": [{"window_id": option["window_id"], "cliffhanger": "An answer is still needed.",
                             "why_it_works": "There is a confrontation.", "context_needed": "Two people disagree.",
                             "self_scores": {k: 5 for k in SCORE_KEYS}} for option in options]}


class FakeClient:
    def __init__(self):
        self.http_attempts = 0
        self.cache_hits = 0
        self.prompts = []

    def ask_json(self, prompt, required):
        self.prompts.append(prompt)
        self.http_attempts += 1
        return result_for(prompt, required), []


class Stage4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "input"
        self.source.mkdir()
        for n in (1, 2, 3):
            (self.source / f"episode-{n:03d}.txt").write_text(screenplay_text(n), encoding="utf-8")
        self.episodes, _ = load_show(self.source)
        self.config = load_config("config.stage4.local.json")
        self.config.update({"raw_outputs_dir": str(self.root / "outputs"), "cache_dir": str(self.root / "cache"),
                            "episodes_per_minute": 100000, "cross_episode_batch_size": 3})

    def test_action_hook_remains_offered_among_many_dialogue_hooks(self):
        content = screenplay_text(1)
        extra = '\n**ACTION:**\nStart: (00:16.00) | End: (00:17.00)\nThe guard blocks a punch.\n'
        (self.source / "episode-001.txt").write_text(content.replace("--- SCRIPT END ---", extra + "\n--- SCRIPT END ---"), encoding="utf-8")
        episodes, _ = load_show(self.source)
        windows = make_local_windows(episodes[1], max_hooks=3)
        self.assertTrue(any("blocks a punch" in w["hook_text"] for w in windows))

    def test_quote_excerpt_is_verified_but_invention_fails(self):
        option = make_local_windows(self.episodes[1])[0]
        event = next(e for e in self.episodes[1]["events"] if e["event_id"] == option["hook_event_id"])
        c = {"segments": option["segments"], "hook": {"episode": 1, "timestamp": event["start"], "quote": event["text"]},
             "cliffhanger": "Review.", "why_it_works": 'Someone says "lied to me".', "context_needed": "Review."}
        self.assertTrue(validate_candidate(c, self.episodes)["valid"])
        c["why_it_works"] = 'Someone says "A secret planet exploded".'
        self.assertFalse(validate_candidate(c, self.episodes)["valid"])

    def test_full_simulated_pipeline_writes_real_exports_and_audit(self):
        client = FakeClient()
        progress = []
        state, out = execute(str(self.source), self.config, "A", client=client, do_preflight=False, progress=progress.append)
        self.assertEqual(state["status"], "completed", state["errors"])
        self.assertEqual(len(state["episode_passes"]), 3)
        self.assertTrue(any(len(c["segments"]) == 2 for c in state["candidates"]))
        self.assertEqual(len(state["judgements"]), len(state["candidates"]))
        for name in ("promos.json", "promos.csv", "ranked_report.md", "eval_report.md", "manual_audit.md", "manual_audit.csv", "WHERE_AI_GOT_IT_WRONG.md"):
            self.assertTrue((out / name).is_file(), name)
        self.assertIn("verdict_good_or_bad", (out / "manual_audit.csv").read_text(encoding="utf-8-sig"))
        self.assertTrue(all(validate_candidate(c, self.episodes)["valid"] for c in state["candidates"]))
        self.assertTrue(all("Run label A" in prompt for prompt in client.prompts))
        self.assertEqual(progress[-1]["phase"], "done")
        self.assertTrue(any(update["phase"] == "episodes" for update in progress))
        self.assertTrue(any(update["phase"] == "judge" for update in progress))

    def test_cache_reuses_successful_calls_on_restart(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body
        calls = []
        def opener(request, timeout):
            calls.append(request)
            prompt = json.loads(request.data)["messages"][0]["content"]
            key = "judgements" if "\nCANDIDATE MATERIAL:\n" in prompt else "candidates"
            body = {"message": {"content": json.dumps(result_for(prompt, key))}, "done": True,
                    "prompt_eval_count": 100, "eval_count": 10}
            return Response(json.dumps(body).encode())
        cfg = copy.deepcopy(self.config)
        cfg["raw_outputs_dir"] = str(Path(cfg["raw_outputs_dir"]) / "run_A")
        first_client = MeteredClient(cfg)
        first_client.opener, first_client.sleep = opener, lambda _: None
        first, _ = execute(str(self.source), self.config, "A", client=first_client, do_preflight=False)
        first_count = len(calls)
        second_client = MeteredClient(cfg)
        second_client.opener, second_client.sleep = opener, lambda _: None
        second, _ = execute(str(self.source), self.config, "A", client=second_client, do_preflight=False)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertGreater(first_count, 0)
        self.assertEqual(len(calls), first_count)
        self.assertEqual(second["cache_hits"], first_count)
        self.assertEqual(second["usage"]["new_input_tokens"], 0)

    def test_interrupted_run_saves_partial_results(self):
        class Interrupted(FakeClient):
            def ask_json(self, prompt, required):
                if self.http_attempts:
                    raise KeyboardInterrupt()
                return super().ask_json(prompt, required)
        state, out = execute(str(self.source), self.config, "A", client=Interrupted(), do_preflight=False)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["metrics"]["episodes_processed"], 1)
        self.assertTrue((out / "checkpoint.json").is_file())
        self.assertTrue((out / "promos.json").is_file())

    def test_failed_episode_is_not_counted_as_processed(self):
        class OneFailure(FakeClient):
            def ask_json(self, prompt, required):
                if "\nEPISODE DATA:\n" in prompt and json.loads(prompt.split("\nEPISODE DATA:\n", 1)[1])["episode"] == 2:
                    raise ProviderError("simulated failure")
                return super().ask_json(prompt, required)
        state, _ = execute(str(self.source), self.config, "A", client=OneFailure(), do_preflight=False)
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["metrics"]["episodes_attempted"], 3)
        self.assertEqual(state["metrics"]["episodes_processed"], 2)

    def test_comparison_updates_both_evaluation_reports(self):
        _, left = execute(str(self.source), self.config, "A", client=FakeClient(), do_preflight=False)
        _, right = execute(str(self.source), self.config, "B", client=FakeClient(), do_preflight=False)
        output = self.root / "stability.md"
        result = subprocess.run([sys.executable, "compare_runs.py", "--a", str(left / "promos.json"),
                                 "--b", str(right / "promos.json"), "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Exact shared promos", output.read_text(encoding="utf-8"))
        self.assertIn("Compared run A", (left / "eval_report.md").read_text(encoding="utf-8"))
        self.assertIn("Compared run A", (right / "eval_report.md").read_text(encoding="utf-8"))

    def test_judge_must_return_the_correct_hook_event_id(self):
        option = make_local_windows(self.episodes[1])[0]
        e = next(e for e in self.episodes[1]["events"] if e["event_id"] == option["hook_event_id"])
        candidate = {"candidate_id": "C1", "segments": option["segments"], "hook_event_id": e["event_id"],
                     "hook": {"episode": 1, "timestamp": e["start"], "quote": e["text"]},
                     "cliffhanger": "Review.", "why_it_works": "Review.", "context_needed": "Review."}
        response = {"judgements": [{"candidate_id": "C1", "hook_event_id": "invented",
                                    "exact_hook_quote": e["text"],
                                    "scores": {k: {"score": 5, "justification": "A test."} for k in SCORE_KEYS}}]}
        accepted, rejected = _judge_response(response, [candidate], self.episodes)
        self.assertFalse(accepted)
        self.assertTrue(rejected)

    def test_overlap_uses_episode_and_time(self):
        a = {"segments": [{"episode": 1, "start": "00:00.00", "end": "00:30.00"}]}
        b = {"segments": [{"episode": 1, "start": "00:10.00", "end": "00:30.00"}]}
        c = {"segments": [{"episode": 2, "start": "00:00.00", "end": "00:30.00"}]}
        self.assertAlmostEqual(overlap_iou(a, b), 2 / 3)
        self.assertEqual(overlap_iou(a, c), 0)

    def test_high_spoiler_risk_reduces_score(self):
        option = make_local_windows(self.episodes[1])[0]
        c = {"segments": option["segments"]}
        low = {"scores": {k: {"score": 5} for k in SCORE_KEYS}}
        low["scores"]["spoiler_risk"]["score"] = 1
        high = copy.deepcopy(low)
        high["scores"]["spoiler_risk"]["score"] = 10
        self.assertGreater(score_candidate(c, low, self.episodes, self.config["scoring_weights"])["base_score"],
                           score_candidate(c, high, self.episodes, self.config["scoring_weights"])["base_score"])

    def test_compare_requires_distinct_runs_and_same_show(self):
        candidate = {"segments": [{"episode": 1, "start": "00:00.00", "end": "00:30.00"}]}
        left = {"run_label": "A", "status": "completed", "show_fingerprint": "same", "generation_settings": {}, "ranked": [candidate]}
        right = dict(left, run_label="B")
        self.assertEqual(compare(left, right)["exact_matches"], 1)
        with self.assertRaises(ValueError): compare(left, left)
        with self.assertRaises(ValueError): compare(left, dict(right, show_fingerprint="different"))


if __name__ == "__main__":
    unittest.main()
