import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import screenplay_text
from llm_client import LLMClient, load_config
from run_stage3 import JUDGE_KEYS, prepare_candidate, prepare_judgements, run
from screenplay import load_show


def proposal(number):
    return {
        "segments": [{"episode": number, "start": "00:00.00", "end": "00:30.00"}],
        "hook_event_id": f"E{number:03d}-0003",
        "cliffhanger": "Whether the characters will reconcile remains unclear.",
        "why_it_works": "A confrontation introduces a conflict.",
        "context_needed": "Two people are arguing.",
    }


class FakeClient:
    def __init__(self):
        self.http_attempts = 0
        self.cache_hits = 0
        self.calls = []
    def ask_json(self, prompt, required_list):
        self.calls.append(required_list)
        if required_list == "judgements":
            material = json.loads(prompt.split("\nCANDIDATE MATERIAL:\n", 1)[1])
            return {"judgements": [{
                "candidate_id": item["candidate_id"],
                "exact_hook_quote": item["hook"]["quote"],
                "scores": {key: {"score": 5, "justification": "This is a simulated test score."} for key in JUDGE_KEYS},
            } for item in material]}, []
        if "\nEPISODE DATA:\n" in prompt:
            episode_data = json.loads(prompt.split("\nEPISODE DATA:\n", 1)[1])
            if "candidate_windows" in episode_data:
                return {"candidates": [{
                    "window_id": episode_data["candidate_windows"][0]["window_id"],
                    "cliffhanger": "Whether the characters will reconcile remains unclear.",
                    "why_it_works": "A confrontation introduces a conflict.",
                    "context_needed": "Two people are arguing.",
                }]}, []
            return {"candidates": [proposal(episode_data["episode"])]}, []
        return {"candidates": []}, []


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input_dir = self.root / "input"
        self.input_dir.mkdir()
        for number in (1, 2, 3):
            (self.input_dir / f"episode-{number:03d}.txt").write_text(screenplay_text(number), encoding="utf-8")
        self.episodes, _ = load_show(self.input_dir)
        self.config = load_config("config.json")
        self.config.update({"cache_dir": str(self.root / "cache"), "raw_outputs_dir": str(self.root / "raw")})

    def test_three_episode_pipeline_with_simulated_model(self):
        client = FakeClient()
        report = run(str(self.input_dir), [1, 2, 3], self.config, client=client)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(report["candidates"]), 3)
        self.assertEqual(len(report["judgements"]), 3)
        self.assertEqual(client.calls, ["candidates", "candidates", "candidates", "candidates", "judgements"])
        self.assertTrue((self.root / "raw" / "stage3_results.json").is_file())

    def test_pipeline_uses_client_logs_and_cache_end_to_end(self):
        class Response:
            status = 200
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return self.body
        fake = FakeClient()
        requests = []
        def opener(request, timeout):
            requests.append(request)
            prompt = json.loads(request.data)["contents"][0]["parts"][0]["text"]
            required = "judgements" if "\nCANDIDATE MATERIAL:\n" in prompt else "candidates"
            output, _ = fake.ask_json(prompt, required)
            wrapped = {"candidates": [{"content": {"parts": [{"text": json.dumps(output)}]}}]}
            return Response(json.dumps(wrapped).encode())
        self.config.update({"api_key_env": "KUKU_PIPELINE_TEST_KEY", "episodes_per_minute": 100000})
        with patch.dict("os.environ", {"KUKU_PIPELINE_TEST_KEY": "test-secret-not-real-123456789"}):
            client = LLMClient(self.config, opener=opener, sleep=lambda _: None)
            first = run(str(self.input_dir), [1, 2, 3], self.config, client=client)
            second = run(str(self.input_dir), [1, 2, 3], self.config, client=client)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(requests), 5)
        self.assertEqual(client.cache_hits, 5)
        self.assertEqual(len(list((self.root / "raw" / "requests").glob("*.json"))), 5)
        self.assertEqual(len(list((self.root / "raw" / "responses").glob("*.success.body"))), 5)

    def test_small_timestamp_correction_is_recorded(self):
        raw = proposal(1)
        raw["segments"][0]["end"] = "00:30.10"
        candidate, errors, corrections = prepare_candidate(raw, "EP001-C01", self.episodes, self.config, 1)
        self.assertEqual(errors, [])
        self.assertEqual(candidate["segments"][0]["end"], "00:30.00")
        self.assertEqual(len(corrections), 1)

    def test_mid_dialogue_boundary_cannot_be_snapped_too_far(self):
        raw = proposal(1)
        raw["segments"][0]["end"] = "00:25.00"
        candidate, errors, _ = prepare_candidate(raw, "EP001-C01", self.episodes, self.config, 1)
        self.assertIsNone(candidate)
        self.assertTrue(any("cannot be snapped" in error for error in errors))

    def test_model_cannot_reference_unknown_hook(self):
        raw = proposal(1)
        raw["hook_event_id"] = "invented"
        candidate, errors, _ = prepare_candidate(raw, "EP001-C01", self.episodes, self.config, 1)
        self.assertIsNone(candidate)
        self.assertTrue(any("does not exist" in error for error in errors))

    def test_cross_episode_pass_requires_at_least_two_episodes(self):
        candidate, errors, _ = prepare_candidate(proposal(1), "CROSS-C01", self.episodes, self.config, require_cross=True)
        self.assertIsNone(candidate)
        self.assertTrue(any("fewer than two" in error for error in errors))

    def test_judge_cannot_invent_a_hook_quote(self):
        candidate, errors, _ = prepare_candidate(proposal(1), "EP001-C01", self.episodes, self.config, 1)
        self.assertFalse(errors)
        scores = {key: {"score": 5, "justification": "A simulated test."} for key in JUDGE_KEYS}
        response = {"judgements": [{"candidate_id": "EP001-C01", "exact_hook_quote": "Invented!", "scores": scores}]}
        accepted, rejected = prepare_judgements(response, [candidate], self.episodes)
        self.assertEqual(accepted, [])
        self.assertTrue(any("does not match" in item for item in rejected[0]["errors"]))

    def test_multiline_judge_justification_is_rejected(self):
        candidate, errors, _ = prepare_candidate(proposal(1), "EP001-C01", self.episodes, self.config, 1)
        self.assertFalse(errors)
        scores = {key: {"score": 5, "justification": "Line one.\nLine two."} for key in JUDGE_KEYS}
        response = {"judgements": [{"candidate_id": "EP001-C01", "exact_hook_quote": candidate["hook"]["quote"], "scores": scores}]}
        accepted, rejected = prepare_judgements(response, [candidate], self.episodes)
        self.assertEqual(accepted, [])
        self.assertTrue(any("must be one line" in item for item in rejected[0]["errors"]))

    def test_detected_errors_are_logged_once_across_reruns(self):
        class InvalidHookClient(FakeClient):
            def ask_json(self, prompt, required_list):
                response, errors = super().ask_json(prompt, required_list)
                if "\nEPISODE DATA:\n" in prompt:
                    response["candidates"][0]["hook_event_id"] = "not-an-event"
                return response, errors
        run(str(self.input_dir), [1, 2, 3], self.config, client=InvalidHookClient())
        path = self.root / "raw" / "WHERE_AI_GOT_IT_WRONG.md"
        self.assertTrue(path.is_file())
        first = path.read_text(encoding="utf-8")
        run(str(self.input_dir), [1, 2, 3], self.config, client=InvalidHookClient())
        self.assertEqual(path.read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
