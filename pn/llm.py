"""One way to call the model: GPT-6.1 Terra on OpenRouter, strict JSON out, cached on disk.

Every call is keyed by a hash of (model, task, prompt version, payload). The same input always
returns the same cached answer, so a rebuild is fast and the demo is repeatable. Only new input
reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "llm"
URL = "https://openrouter.ai/api/v1/chat/completions"


def _env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith(("OPENROUTER_", "PN_"))})
    return env


ENV = _env()
MODEL = ENV.get("PN_MODEL", "openai/gpt-6.1-terra")
REASONING = ENV.get("PN_REASONING", "medium")


class LLMError(RuntimeError):
    pass


def cache_key(task: str, version: str, system: str, user: str, schema: dict) -> str:
    blob = json.dumps([MODEL, REASONING, task, version, system, user, schema], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def call_json(task: str, version: str, system: str, user: str, schema: dict,
              *, offline: bool = False) -> dict:
    """Return the model's JSON answer for this prompt, from cache when possible."""
    key = cache_key(task, version, system, user, schema)
    path = CACHE / f"{task}-{key}.json"
    if path.exists():
        return json.loads(path.read_text())["output"]
    if offline:
        raise LLMError(f"not cached and offline: {task}")
    api_key = ENV.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY is not set (see .env.example)")
    body = {
        "model": MODEL,
        "reasoning": {"effort": REASONING},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": task, "strict": True, "schema": schema}},
    }
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(URL, json=body, timeout=180,
                           headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            output = json.loads(content)
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"task": task, "model": data.get("model", MODEL),
                                        "usage": data.get("usage"), "output": output}, indent=1))
            return output
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:  # retry, then fail loudly
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"{task} failed after 3 tries: {last}")


# ---- tiny helpers for writing strict JSON schemas by hand ----

def obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


STR = {"type": "string"}
NSTR = {"type": ["string", "null"]}
BOOL = {"type": "boolean"}
