"""
Thin client for NVIDIA's NIM API (build.nvidia.com), used for three
separate capabilities that each have their own API key + model choice
configured in Settings: general chat, coding, and image generation.

Chat completions use NVIDIA's OpenAI-compatible endpoint. Image generation
uses the same style of API, but the exact hosted endpoint has shifted
around NVIDIA's catalog in the past (self-hosted NIM containers document
POST /v1/images/generations; some hosted playground models have used a
separate function-invoke URL). The endpoint is stored in Settings rather
than hardcoded so it can be corrected there if NVIDIA's hosted route for
your chosen model differs -- check the "Get API Key" / code sample panel
on your model's build.nvidia.com page if image generation errors out.
"""

import requests

CHAT_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
REQUEST_TIMEOUT_CHAT = 60
REQUEST_TIMEOUT_IMAGE = 90


class AIConfigError(Exception):
    """Raised when a category has no API key / model configured yet."""


class AIRequestError(Exception):
    """Raised when the NVIDIA API call itself fails or returns something unexpected."""


def chat_completion(category_settings: dict, messages: list, max_tokens: int = 800, temperature: float = 0.6) -> str:
    api_key = (category_settings or {}).get("api_key")
    model = (category_settings or {}).get("model")

    if not api_key or not model:
        raise AIConfigError(
            "No API key or model configured for this category yet. "
            "An admin can set this in the Settings tab."
        )

    try:
        resp = requests.post(
            CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
            timeout=REQUEST_TIMEOUT_CHAT,
        )
    except requests.RequestException as e:
        raise AIRequestError(f"Couldn't reach NVIDIA's API: {e}")

    if resp.status_code != 200:
        raise AIRequestError(f"NVIDIA API error {resp.status_code}: {resp.text[:400]}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise AIRequestError(f"Unexpected response format from NVIDIA API: {e}")


def generate_image(image_settings: dict, prompt: str) -> str:
    """Returns a base64-encoded PNG/JPEG string (no data: prefix)."""
    api_key = (image_settings or {}).get("api_key")
    model = (image_settings or {}).get("model")
    endpoint = (image_settings or {}).get("endpoint") or "https://integrate.api.nvidia.com/v1/images/generations"

    if not api_key or not model:
        raise AIConfigError(
            "No API key or model configured for image generation yet. "
            "An admin can set this in the Settings tab."
        )

    try:
        resp = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "prompt": prompt,
                "n": 1,
                "response_format": "b64_json",
                "seed": 0,
                "steps": 4,
            },
            timeout=REQUEST_TIMEOUT_IMAGE,
        )
    except requests.RequestException as e:
        raise AIRequestError(f"Couldn't reach NVIDIA's image API: {e}")

    if resp.status_code != 200:
        raise AIRequestError(f"NVIDIA image API error {resp.status_code}: {resp.text[:400]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise AIRequestError(f"Unexpected response from NVIDIA image API: {e}")

    # Try the OpenAI-compatible shape first, then fall back to the legacy
    # NVCF shape some hosted models still use.
    b64 = None
    if isinstance(data.get("data"), list) and data["data"]:
        b64 = data["data"][0].get("b64_json")
    if not b64 and isinstance(data.get("artifacts"), list) and data["artifacts"]:
        b64 = data["artifacts"][0].get("base64")

    if not b64:
        raise AIRequestError(f"Couldn't find image data in the response: {str(data)[:400]}")

    return b64
