import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from llm_client import LLMClient, ProviderError, load_config


class FakeResponse:
    status = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.data


def gemini_body(text):
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_config("config.json")
        self.config.update({
            "cache_dir": str(self.root / "cache"),
            "raw_outputs_dir": str(self.root / "raw"),
            "api_key_env": "KUKU_TEST_SECRET",
            "episodes_per_minute": 100000,
        })
        self.secret = "unit-test-secret-unique-and-not-real"
        patcher = patch.dict(os.environ, {"KUKU_TEST_SECRET": self.secret})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gemini_raw_bodies_and_cache(self):
        captured = []
        response = gemini_body('{"candidates":[]}')
        def opener(request, timeout):
            captured.append((request.full_url, request.data, timeout))
            return FakeResponse(response)
        client = LLMClient(self.config, opener=opener, sleep=lambda _: None)
        self.assertEqual(client.ask_json("a prompt", "candidates")[0], {"candidates": []})
        self.assertEqual(client.ask_json("a prompt", "candidates")[0], {"candidates": []})
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0][0].endswith("/models/gemini-3.1-flash-lite:generateContent"))
        self.assertNotIn("temperature", json.loads(captured[0][1])["generationConfig"])
        request_log = next((self.root / "raw" / "requests").glob("*.json"))
        response_log = next((self.root / "raw" / "responses").glob("*.success.body"))
        self.assertEqual(request_log.read_bytes(), captured[0][1])
        self.assertEqual(response_log.read_bytes(), response)
        self.assertNotIn(self.secret.encode(), request_log.read_bytes())
        self.assertEqual(client.cache_hits, 1)

    def test_recovers_successful_response_if_cache_write_was_lost(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return FakeResponse(gemini_body('{"candidates":[]}'))
        client = LLMClient(self.config, opener=opener, sleep=lambda _: None)
        client.ask_json("recover this", "candidates")
        for file in (self.root / "cache").glob("*.response"):
            file.unlink()
        self.assertEqual(client.ask_json("recover this", "candidates")[0], {"candidates": []})
        self.assertEqual(len(calls), 1)

    def test_openai_compatible_request_and_response(self):
        self.config.update({"provider": "openai_compatible", "base_url": "https://example.test/v1", "model": "example-model"})
        captured = []
        def opener(request, timeout):
            captured.append(request)
            return FakeResponse(json.dumps({"choices": [{"message": {"content": '{"candidates":[]}'}}]}).encode())
        client = LLMClient(self.config, opener=opener, sleep=lambda _: None)
        self.assertEqual(client.ask_json("hello", "candidates")[0], {"candidates": []})
        self.assertEqual(captured[0].full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(json.loads(captured[0].data)["model"], "example-model")
        self.assertNotIn(self.secret.encode(), captured[0].data)

    def test_429_waits_and_retries_with_raw_error_logged(self):
        calls, sleeps = [], []
        error_body = b'{"error":{"message":"rate limited"}}'
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "limit", {"Retry-After": "6"}, io.BytesIO(error_body))
            return FakeResponse(gemini_body('{"candidates":[]}'))
        client = LLMClient(self.config, opener=opener, sleep=sleeps.append)
        self.assertEqual(client.ask_json("rate", "candidates")[0], {"candidates": []})
        self.assertEqual(len(calls), 2)
        self.assertIn(6.0, sleeps)
        error_log = next((self.root / "raw" / "responses").glob("*.error.body"))
        self.assertEqual(error_log.read_bytes(), error_body)

    def test_bad_json_retried_once_and_both_calls_cached(self):
        outputs = [gemini_body("not JSON"), gemini_body('{"candidates":[]}')]
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return FakeResponse(outputs[len(calls) - 1])
        client = LLMClient(self.config, opener=opener, sleep=lambda _: None)
        data, errors = client.ask_json("repair", "candidates")
        self.assertEqual(data, {"candidates": []})
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(calls), 2)
        data, errors = client.ask_json("repair", "candidates")
        self.assertEqual(data, {"candidates": []})
        self.assertEqual(len(calls), 2)
        self.assertEqual(client.cache_hits, 2)

    def test_two_invalid_responses_are_recorded_and_return_none(self):
        client = LLMClient(self.config, opener=lambda request, timeout: FakeResponse(gemini_body("bad")), sleep=lambda _: None)
        data, errors = client.ask_json("invalid", "candidates")
        self.assertIsNone(data)
        self.assertEqual(len(errors), 2)
        self.assertEqual(len(list((self.root / "raw" / "responses").glob("*.success.body"))), 2)

    def test_response_that_echoes_secret_is_not_written(self):
        client = LLMClient(self.config, opener=lambda request, timeout: FakeResponse(gemini_body(self.secret)), sleep=lambda _: None)
        with self.assertRaisesRegex(ProviderError, "echoed"):
            client.complete("safe prompt")
        for file in (self.root / "raw").rglob("*"):
            if file.is_file():
                self.assertNotIn(self.secret.encode(), file.read_bytes())

    def test_missing_key_is_reported_without_prompting(self):
        with patch.dict(os.environ, {}, clear=True):
            client = LLMClient(self.config, opener=lambda *a, **k: self.fail("Should not call network"))
            with self.assertRaisesRegex(ProviderError, "KUKU_TEST_SECRET"):
                client.complete("uncached prompt")


if __name__ == "__main__":
    unittest.main()
