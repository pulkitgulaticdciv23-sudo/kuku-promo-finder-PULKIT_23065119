"""Configurable local Ollama, Gemini, or OpenAI-compatible client."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, urlopen


class ProviderError(Exception):
    """A provider, configuration, or network failure; safe to display without a key."""


class BadModelJSON(Exception):
    """The provider answered but did not return the requested JSON object."""


class _NoRedirect(HTTPRedirectHandler):
    """Keep a local-only request from being redirected to another host."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_config(path: str | Path) -> dict:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"Could not read configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise ProviderError("Configuration must be a JSON object")
    for field in ("provider", "model", "base_url", "cache_dir", "raw_outputs_dir"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ProviderError(f"Configuration requires a nonempty {field}")
    if config["provider"] not in ("gemini", "openai_compatible", "ollama_local"):
        raise ProviderError("provider must be gemini, openai_compatible, or ollama_local")
    if config["provider"] != "ollama_local" and (
        not isinstance(config.get("api_key_env"), str) or not config["api_key_env"].strip()
    ):
        raise ProviderError("Cloud providers require an api_key_env name")
    url = urlsplit(config["base_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ProviderError("base_url must be an HTTP(S) URL without credentials, query, or fragment")
    if config["provider"] == "ollama_local":
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ProviderError("ollama_local requires an HTTP loopback address on this computer")
        if "cloud" in config["model"].casefold():
            raise ProviderError("ollama_local does not permit cloud model names")
        if type(config.get("context_tokens")) is not int or config["context_tokens"] < 1024:
            raise ProviderError("ollama_local needs context_tokens of at least 1024")
    for field in ("max_candidates_per_episode", "judge_batch_size"):
        if field in config and (type(config[field]) is not int or config[field] < 1):
            raise ProviderError(f"{field} must be a positive integer")
    if not isinstance(config.get("episodes_per_minute"), (int, float)) or config["episodes_per_minute"] <= 0:
        raise ProviderError("episodes_per_minute must be positive")
    for field in ("timeout_seconds", "max_output_tokens", "max_retry_wait_seconds"):
        if not isinstance(config.get(field), (int, float)) or config[field] <= 0:
            raise ProviderError(f"{field} must be positive")
    if type(config.get("max_429_retries")) is not int or config["max_429_retries"] < 0:
        raise ProviderError("max_429_retries must be a nonnegative integer")
    return config


class LLMClient:
    """Each HTTP attempt is logged; successful HTTP bodies are cached by request."""

    def __init__(self, config: dict, *, opener=None, sleep=None, monotonic=None):
        self.config = config
        if opener is not None:
            self.opener = opener
        elif config["provider"] == "ollama_local":
            self.opener = build_opener(ProxyHandler({}), _NoRedirect()).open
        else:
            self.opener = urlopen
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic
        self.last_request_time = None
        self.http_attempts = 0
        self.cache_hits = 0
        self.cache_dir = Path(config["cache_dir"])
        self.raw_dir = Path(config["raw_outputs_dir"])

    def _request_data(self, prompt: str) -> tuple[str, bytes]:
        base = self.config["base_url"].rstrip("/")
        model = self.config["model"]
        if self.config["provider"] == "gemini":
            url = f"{base}/models/{quote(model, safe='-._')}:generateContent"
            generation = {"maxOutputTokens": int(self.config["max_output_tokens"])}
            if self.config.get("temperature") is not None:
                generation["temperature"] = self.config["temperature"]
            if self.config.get("use_json_mode", True):
                generation["responseMimeType"] = "application/json"
            payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": generation}
        elif self.config["provider"] == "ollama_local":
            url = f"{base}/api/chat"
            options = {
                "num_ctx": self.config["context_tokens"],
                "num_predict": int(self.config["max_output_tokens"]),
            }
            if self.config.get("temperature") is not None:
                options["temperature"] = self.config["temperature"]
            payload = {
                "model": model, "messages": [{"role": "user", "content": prompt}],
                "stream": False, "think": False, "truncate": False, "options": options,
            }
            if self.config.get("use_json_mode", True):
                payload["format"] = "json"
        else:
            url = f"{base}/chat/completions"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": int(self.config["max_output_tokens"]),
            }
            if self.config.get("temperature") is not None:
                payload["temperature"] = self.config["temperature"]
            if self.config.get("use_json_mode", True):
                payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return url, body

    def _cache_path(self, url: str, body: bytes) -> Path:
        identity = json.dumps({
            "provider": self.config["provider"], "model": self.config["model"], "url": url,
        }, sort_keys=True).encode("utf-8") + b"\n" + body
        return self.cache_dir / (hashlib.sha256(identity).hexdigest() + ".response")

    def _throttle(self) -> None:
        interval = 60.0 / float(self.config["episodes_per_minute"])
        if self.last_request_time is not None:
            wait = interval - (self.monotonic() - self.last_request_time)
            if wait > 0:
                print(f"Rate limit: waiting {wait:.1f}s before the next request.", flush=True)
                self.sleep(wait)
        self.last_request_time = self.monotonic()

    def _retry_delay(self, attempt: int, headers, body: bytes) -> float:
        delay = min(5.0 * (2 ** attempt), 60.0)
        value = headers.get("Retry-After") if headers is not None else None
        if value:
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    date = parsedate_to_datetime(value)
                    delay = max(delay, (date - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        try:
            obj = json.loads(body)
            for detail in obj.get("error", {}).get("details", []):
                retry = detail.get("retryDelay", "") if isinstance(detail, dict) else ""
                if isinstance(retry, str) and retry.endswith("s"):
                    delay = max(delay, float(retry[:-1]))
        except (ValueError, TypeError, AttributeError):
            pass
        return max(delay, 0.0)

    def _record_response(self, request_id: str, data: bytes, status: str, secret: str, success: bool) -> None:
        # Logging the secret would violate the security requirement. If a provider
        # echoes it, stop rather than write an altered or credential-bearing body.
        if secret and secret.encode("utf-8") in data:
            raise ProviderError("Provider echoed the API key in its response; response was not written to disk")
        suffix = "success" if success else "error"
        atomic_write(self.raw_dir / "responses" / f"{request_id}.{suffix}.body", data)
        metadata = {"status": status, "provider": self.config["provider"], "model": self.config["model"]}
        atomic_write(
            self.raw_dir / "responses" / f"{request_id}.meta.json",
            (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _response_text(self, body: bytes) -> str:
        try:
            response = json.loads(body.decode("utf-8"))
            if self.config["provider"] == "gemini":
                parts = response["candidates"][0]["content"]["parts"]
                text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            elif self.config["provider"] == "ollama_local":
                if response.get("done_reason") == "length":
                    raise ValueError("local model reached the output-token limit")
                prompt_count, output_count = response.get("prompt_eval_count"), response.get("eval_count")
                if type(prompt_count) is int and type(output_count) is int and prompt_count + output_count > self.config["context_tokens"]:
                    raise ValueError("local model exceeded the configured context window")
                text = response["message"]["content"]
            else:
                text = response["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty response")
            return text
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise BadModelJSON(f"Provider response did not contain usable text: {exc}") from exc

    def complete(self, prompt: str) -> str:
        url, body = self._request_data(prompt)
        cache_path = self._cache_path(url, body)
        if cache_path.is_file():
            self.cache_hits += 1
            return self._response_text(cache_path.read_bytes())
        # If a crash occurred after writing the untouched successful response but
        # before writing the cache, recover it using the same request hash.
        saved_responses = sorted((self.raw_dir / "responses").glob(f"{cache_path.stem}__*.success.body"))
        if saved_responses:
            recovered = saved_responses[-1].read_bytes()
            atomic_write(cache_path, recovered)
            self.cache_hits += 1
            return self._response_text(recovered)
        secret = ""
        if self.config["provider"] != "ollama_local":
            env_name = self.config["api_key_env"]
            secret = os.environ.get(env_name, "")
            if not secret:
                raise ProviderError(f"Environment variable {env_name} is not set in this process")
        if secret and secret.encode("utf-8") in body:
            raise ProviderError("API key appears in the request body; refusing to send or log it")
        headers = {"Content-Type": "application/json"}
        if self.config["provider"] == "gemini":
            headers["x-goog-api-key"] = secret
        elif self.config["provider"] == "openai_compatible":
            headers["Authorization"] = f"Bearer {secret}"
        for attempt in range(self.config["max_429_retries"] + 1):
            self._throttle()
            request_id = cache_path.stem + "__" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:10]
            atomic_write(self.raw_dir / "requests" / f"{request_id}.request.json", body)
            self.http_attempts += 1
            request = Request(url, data=body, headers=headers, method="POST")
            try:
                with self.opener(request, timeout=self.config["timeout_seconds"]) as response:
                    response_body = response.read()
                    status = str(response.status)
            except HTTPError as exc:
                response_body = exc.read()
                self._record_response(request_id, response_body, str(exc.code), secret, False)
                if exc.code != 429:
                    if self.config["provider"] == "ollama_local" and exc.code == 400 and b"context" in response_body.lower():
                        raise ProviderError("Local model context limit exceeded; increase context_tokens or reduce the amount of input") from None
                    if self.config["provider"] == "ollama_local" and exc.code == 404:
                        raise ProviderError(f"Local model {self.config['model']} was not found; download it with Ollama") from None
                    raise ProviderError(f"Provider returned HTTP {exc.code}; inspect private raw logs") from None
                if attempt == self.config["max_429_retries"]:
                    raise ProviderError("Provider returned HTTP 429 after all retries; quota may be exhausted") from None
                delay = self._retry_delay(attempt, exc.headers, response_body)
                if delay > self.config["max_retry_wait_seconds"]:
                    raise ProviderError(f"Provider asks for a {delay:.0f}s wait, above the configured maximum; retry later") from None
                print(f"HTTP 429: waiting {delay:.1f}s and retrying ({attempt + 1}/{self.config['max_429_retries']}).", flush=True)
                self.sleep(delay)
                continue
            except (URLError, TimeoutError, OSError) as exc:
                if self.config["provider"] == "ollama_local":
                    raise ProviderError(f"Could not reach local Ollama ({type(exc).__name__}); start Ollama and check that the model is installed") from None
                raise ProviderError(f"Could not reach provider ({type(exc).__name__}); check connection and endpoint") from None
            self._record_response(request_id, response_body, status, secret, True)
            atomic_write(cache_path, response_body)
            return self._response_text(response_body)
        raise ProviderError("Provider retry limit reached")

    def ask_json(self, prompt: str, required_list: str | None = None) -> tuple[dict | None, list[str]]:
        """Retry once if the provider's text is not JSON or lacks a required list."""
        errors = []
        current_prompt = prompt
        for attempt in range(2):
            try:
                value = json.loads(self.complete(current_prompt))
                if not isinstance(value, dict):
                    raise ValueError("top-level JSON must be an object")
                if required_list and not isinstance(value.get(required_list), list):
                    raise ValueError(f"missing {required_list} list")
                return value, errors
            except (BadModelJSON, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"JSON attempt {attempt + 1} failed: {type(exc).__name__}")
                current_prompt = prompt + "\n\nYour previous response was invalid JSON. Return one valid JSON object only, without markdown."
        return None, errors
