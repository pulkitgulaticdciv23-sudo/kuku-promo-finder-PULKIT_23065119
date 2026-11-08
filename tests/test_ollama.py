"""Tests use a fake loopback server; they do not run a real local model."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from fixtures import screenplay_text
from llm_client import BadModelJSON, LLMClient, ProviderError, load_config
from prompts import cross_episode_prompt
from run_stage3 import run
from screenplay import load_show
from test_pipeline import FakeClient


class LocalProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_config("config.local.json")
        self.config.update({
            "cache_dir": str(self.root / "cache"),
            "raw_outputs_dir": str(self.root / "raw"),
            "episodes_per_minute": 100000,
            "timeout_seconds": 5,
        })

    def test_configuration_does_not_need_an_api_key(self):
        self.assertEqual(self.config["provider"], "ollama_local")
        self.assertIsNone(self.config["api_key_env"])

    def test_truncated_or_overflowing_response_is_not_accepted(self):
        client = LLMClient(self.config)
        base = {"message": {"content": '{"candidates":[]}'}, "done": True}
        with self.assertRaises(BadModelJSON):
            client._response_text(json.dumps(dict(base, done_reason="length")).encode())
        with self.assertRaises(BadModelJSON):
            client._response_text(json.dumps(dict(base, prompt_eval_count=16000, eval_count=1000)).encode())

    def test_nonlocal_endpoint_and_cloud_model_are_rejected(self):
        for changed in ({"base_url": "https://example.com"}, {"model": "example:cloud"}):
            with self.subTest(changed=changed):
                bad = dict(self.config, **changed)
                path = self.root / "bad.json"
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ProviderError):
                    load_config(path)

    def test_compact_cross_pass_contains_only_source_windows(self):
        input_dir = self.root / "input"
        input_dir.mkdir()
        for n in (1, 2):
            (input_dir / f"episode-{n:03d}.txt").write_text(screenplay_text(n), encoding="utf-8")
        episodes, _ = load_show(input_dir)
        candidates = [{
            "candidate_id": f"EP{n:03d}-C01",
            "segments": [{"episode": n, "start": "00:00.00", "end": "00:30.00"}],
            "hook": {"episode": n, "timestamp": "00:01.00", "quote": '"You lied to me."'},
            "cliffhanger": "Review needed.",
        } for n in (1, 2)]
        compact = cross_episode_prompt(list(episodes.values()), candidates, compact=True)
        data = json.loads(compact.split("\nSELECTED EPISODES AND SOURCE EVENTS:\n", 1)[1])
        self.assertEqual(len(data), 2)
        self.assertTrue(all(event["type"] in ("dialogue", "action") for episode in data for event in episode["events"]))
        self.assertTrue(all(episode["source_windows"][0]["end"] == "00:30.00" for episode in data))

    def test_local_http_pipeline_requires_no_key_and_reuses_cache(self):
        captured = []
        fake = FakeClient()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request = json.loads(body)
                captured.append({"path": self.path, "authorization": self.headers.get("Authorization"), "request": request})
                prompt = request["messages"][0]["content"]
                required = "judgements" if "\nCANDIDATE MATERIAL:\n" in prompt else "candidates"
                result, _ = fake.ask_json(prompt, required)
                response = json.dumps({"message": {"role": "assistant", "content": json.dumps(result)}, "done": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        self.config["base_url"] = f"http://127.0.0.1:{server.server_port}"
        input_dir = self.root / "input"
        input_dir.mkdir()
        for n in (1, 2, 3):
            (input_dir / f"episode-{n:03d}.txt").write_text(screenplay_text(n), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            client = LLMClient(self.config, sleep=lambda _: None)
            first = run(str(input_dir), [1, 2, 3], self.config, client=client)
            second = run(str(input_dir), [1, 2, 3], self.config, client=client)
        self.assertEqual(first["status"], "completed", first["errors"])
        self.assertEqual(second["status"], "completed", second["errors"])
        self.assertEqual(len(captured), 6)  # 3 episodes, cross, and 2 judge batches.
        self.assertEqual(client.cache_hits, 6)
        self.assertTrue(all(item["path"] == "/api/chat" for item in captured))
        self.assertTrue(all(item["authorization"] is None for item in captured))
        self.assertTrue(all(item["request"]["truncate"] is False for item in captured))
        self.assertTrue(all(item["request"]["options"]["num_ctx"] == 16384 for item in captured))


if __name__ == "__main__":
    unittest.main()
