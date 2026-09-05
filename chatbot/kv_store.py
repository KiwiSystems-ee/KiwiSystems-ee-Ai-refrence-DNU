"""
Storage backend: Upstash Redis over its REST API.

Why: on serverless hosts like Vercel, there's no persistent local
filesystem -- every request can run in a fresh, isolated container, and
only /tmp is writable (and it's wiped between invocations). Local JSON
files (users.json, learned_intents.json, settings.json) would silently
vanish. Upstash's REST API is plain HTTPS with no persistent connection
needed, which is exactly what a serverless function can use reliably.

This also works from a normal always-on host (PythonAnywhere, your own
server, etc.) -- it's just an HTTPS call, not something serverless-specific.
So moving to this backend isn't a Vercel-only trade-off; it removes the
"local disk quota" concern everywhere, too.

Everything here stores/retrieves whole JSON blobs under a small number of
fixed keys (one per concern: "users", "learned_intents", "settings"),
mirroring the shape the app used when these were separate local files.
"""

import json
import os
import requests

REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
REQUEST_TIMEOUT = 15


class KVConfigError(Exception):
    """Raised when Upstash isn't configured (missing env vars)."""


class KVRequestError(Exception):
    """Raised when the Upstash REST call itself fails."""


def _command(*args) -> dict:
    if not REST_URL or not REST_TOKEN:
        raise KVConfigError(
            "Storage isn't configured: set UPSTASH_REDIS_REST_URL and "
            "UPSTASH_REDIS_REST_TOKEN as environment variables."
        )
    try:
        resp = requests.post(
            REST_URL,
            headers={"Authorization": f"Bearer {REST_TOKEN}"},
            json=list(args),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise KVRequestError(f"Couldn't reach Upstash: {e}")

    if resp.status_code != 200:
        raise KVRequestError(f"Upstash error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    if "error" in data:
        raise KVRequestError(f"Upstash command error: {data['error']}")
    return data


def get_json(key: str, default=None):
    """Fetch a key and parse it as JSON. Returns `default` if the key doesn't exist."""
    result = _command("GET", key).get("result")
    if result is None:
        return default
    return json.loads(result)


def set_json(key: str, value, ex_seconds: int = None):
    """Store a value as a JSON string, optionally with an expiry (seconds)."""
    payload = json.dumps(value)
    if ex_seconds:
        _command("SET", key, payload, "EX", str(ex_seconds))
    else:
        _command("SET", key, payload)


def delete(key: str):
    _command("DEL", key)
