"""
Stores admin-configurable settings: NVIDIA API keys and model names for
each capability (general chat, coding, image generation), plus the list
of custom behavior rules set via /rule in the admin console.

Everything lives in settings.json. API keys are stored as-is (not
encrypted) since this is a small self-hosted app with no separate secrets
manager -- treat settings.json with the same care as a password file.
"""

import threading

import kv_store

STORE_KEY = "settings"
_lock = threading.Lock()

DEFAULT_MODELS = {
    "general": "meta/llama-3.3-70b-instruct",
    "coding": "qwen/qwen2.5-coder-32b-instruct",
    "image": "black-forest-labs/flux.1-schnell",
}

DEFAULT_IMAGE_ENDPOINT = "https://integrate.api.nvidia.com/v1/images/generations"


def _defaults() -> dict:
    return {
        "categories": {
            "general": {"api_key": "", "model": DEFAULT_MODELS["general"]},
            "coding": {"api_key": "", "model": DEFAULT_MODELS["coding"]},
            "image": {"api_key": "", "model": DEFAULT_MODELS["image"], "endpoint": DEFAULT_IMAGE_ENDPOINT},
        },
        "email_2fa": {"service_id": "", "template_id": "", "public_key": "", "private_key": ""},
        "rules": [],
    }


def _load() -> dict:
    data = kv_store.get_json(STORE_KEY, default=None)
    if data is None:
        return _defaults()
    # Fill in anything missing (e.g. after an upgrade) without clobbering existing values.
    defaults = _defaults()
    for cat, fields in defaults["categories"].items():
        data.setdefault("categories", {}).setdefault(cat, {})
        for key, val in fields.items():
            data["categories"][cat].setdefault(key, val)
    data.setdefault("rules", [])
    data.setdefault("email_2fa", defaults["email_2fa"])
    for key, val in defaults["email_2fa"].items():
        data["email_2fa"].setdefault(key, val)
    return data


def _save(data: dict):
    kv_store.set_json(STORE_KEY, data)


def get_settings() -> dict:
    """Full settings dict, safe to return to the admin UI as-is."""
    return _load()


def get_category(name: str) -> dict:
    return _load()["categories"].get(name, {})


def update_category(name: str, api_key: str = None, model: str = None, endpoint: str = None):
    with _lock:
        data = _load()
        cat = data["categories"].setdefault(name, {})
        if api_key is not None:
            cat["api_key"] = api_key
        if model is not None:
            cat["model"] = model
        if endpoint is not None:
            cat["endpoint"] = endpoint
        _save(data)
        return cat


def get_rules() -> list:
    return _load()["rules"]


def add_rule(text: str) -> list:
    with _lock:
        data = _load()
        data["rules"].append(text.strip())
        _save(data)
        return data["rules"]


def remove_rule(index_from_end: int) -> bool:
    """Remove the Nth most recently added rule (1 = most recent)."""
    with _lock:
        data = _load()
        rules = data["rules"]
        if index_from_end < 1 or index_from_end > len(rules):
            return False
        del rules[-index_from_end]
        _save(data)
        return True


def get_email_2fa() -> dict:
    return _load()["email_2fa"]


def update_email_2fa(service_id: str = None, template_id: str = None, public_key: str = None, private_key: str = None):
    with _lock:
        data = _load()
        cfg = data["email_2fa"]
        if service_id is not None:
            cfg["service_id"] = service_id
        if template_id is not None:
            cfg["template_id"] = template_id
        if public_key is not None:
            cfg["public_key"] = public_key
        if private_key is not None:
            cfg["private_key"] = private_key
        _save(data)
        return cfg
