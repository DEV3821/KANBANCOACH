"""Minimal Ollama API client for Phase 3 draft generation.

Uses urllib (stdlib) to avoid adding the `requests` dependency.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


def check_ollama(base_url: str, timeout: int = 5) -> tuple[bool, str, list[str]]:
    """Check if Ollama is reachable and list available models.

    Returns:
        (reachable: bool, message: str, models: list[str])
    """
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            return True, f"Ollama reachable ({len(models)} models)", models
    except urllib.error.URLError as e:
        return False, f"Ollama not reachable: {e.reason}", []
    except Exception as e:
        return False, f"Ollama check failed: {e}", []


def check_model_available(base_url: str, model: str, timeout: int = 5) -> tuple[bool, str]:
    """Check if a specific model is available in Ollama."""
    ok, msg, models = check_ollama(base_url, timeout)
    if not ok:
        return False, msg
    if model in models:
        return True, f"Model '{model}' available"
    # Check without tag suffix
    base = model.split(":")[0] if ":" in model else model
    for m in models:
        if m.startswith(base):
            return True, f"Model '{model}' available (as '{m}')"
    return False, f"Model '{model}' not found among {len(models)} available models"


def generate(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
    max_retries: int = 2,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Call Ollama generate API with a system + user prompt.

    Returns:
        (success: bool, message: str, parsed_json: dict | None)
    """
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_predict": 1024,
        },
    }

    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                result = json.loads(raw)
                response_text = result.get("response", "").strip()

                # Parse JSON from response
                try:
                    parsed = json.loads(response_text)
                    return True, "OK", parsed
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown fences
                    if "```json" in response_text:
                        json_str = response_text.split("```json")[1].split("```")[0].strip()
                        try:
                            parsed = json.loads(json_str)
                            return True, "Extracted from markdown", parsed
                        except json.JSONDecodeError:
                            pass
                    if "```" in response_text:
                        json_str = response_text.split("```")[1].split("```")[0].strip()
                        try:
                            parsed = json.loads(json_str)
                            return True, "Extracted from fences", parsed
                        except json.JSONDecodeError:
                            pass
                    return False, f"Invalid JSON response: {response_text[:300]}", None

        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.reason}"
            if attempt < max_retries:
                time.sleep(1)
        except urllib.error.URLError as e:
            last_error = f"URL error: {e.reason}"
            if attempt < max_retries:
                time.sleep(2)
        except Exception as e:
            last_error = f"Error: {e}"
            break

    return False, last_error, None
