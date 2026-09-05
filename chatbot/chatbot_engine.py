"""
Shared "brain" for the chatbot: training, inference, and teaching.

Model: TF-IDF + cosine similarity (retrieval-based). See README.md for why.

Two data sources are combined into one live model:
  - intents.json          -> the base dataset you started with
  - learned_intents.json  -> everything taught via the admin /teach flow

Every taught example is appended to learned_intents.json (so it survives
restarts) and the model is retrained in-memory immediately (retraining is
just re-fitting a TF-IDF matrix, which takes milliseconds for this dataset
size -- there is no slow "training run" like with a neural net).
"""

import random
import threading
import json
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import kv_store

# The base dataset ships with the code, so it's fine to read as a local
# file even on serverless hosts (that filesystem is read-only but present).
# Everything *written* at runtime (taught facts) goes through kv_store
# instead, since serverless hosts have no persistent local disk.
BASE_DATA_PATH = Path(__file__).parent / "intents.json"
LEARNED_STORE_KEY = "learned_intents"

CONFIDENCE_THRESHOLD = 0.25

_lock = threading.Lock()  # guards retraining / writes

# Live state, rebuilt by train()
_vectorizer = None
_pattern_vectors = None
_all_patterns = []
_pattern_tags = []
_tag_to_intent = {}


def _load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r") as f:
        return json.load(f)


def train():
    """(Re)build the TF-IDF model from base + learned intents."""
    global _vectorizer, _pattern_vectors, _all_patterns, _pattern_tags, _tag_to_intent

    base = _load_json(BASE_DATA_PATH, {"intents": []})["intents"]
    try:
        learned = kv_store.get_json(LEARNED_STORE_KEY, default={"intents": []})["intents"]
    except (kv_store.KVConfigError, kv_store.KVRequestError):
        # Storage isn't reachable yet (e.g. Upstash env vars not set). Keep
        # the app running on the base dataset alone rather than crashing --
        # taught facts just won't be available as context until it's configured.
        learned = []

    combined = base + learned

    all_patterns, pattern_tags, tag_to_intent = [], [], {}
    for intent in combined:
        # If the same tag appears in both files, merge patterns/responses.
        if intent["tag"] in tag_to_intent:
            tag_to_intent[intent["tag"]]["patterns"].extend(intent["patterns"])
            tag_to_intent[intent["tag"]]["responses"].extend(intent["responses"])
        else:
            tag_to_intent[intent["tag"]] = {
                "tag": intent["tag"],
                "patterns": list(intent["patterns"]),
                "responses": list(intent["responses"]),
            }
        for pattern in intent["patterns"]:
            all_patterns.append(pattern)
            pattern_tags.append(intent["tag"])

    # No stopword removal: short taught questions (e.g. "who made you") can be
    # made entirely of common words ("who", "made", "you"), and stripping them
    # would leave an empty, unmatchable vector. TF-IDF's own weighting already
    # naturally downweights very common words for a corpus this size.
    vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2))
    if all_patterns:
        pattern_vectors = vectorizer.fit_transform(all_patterns)
    else:
        pattern_vectors = None

    _vectorizer = vectorizer
    _pattern_vectors = pattern_vectors
    _all_patterns = all_patterns
    _pattern_tags = pattern_tags
    _tag_to_intent = tag_to_intent


def get_response(user_message: str) -> dict:
    if not _all_patterns:
        return {"response": "I haven't been taught anything yet!", "matched_intent": "fallback", "confidence": 0.0}

    user_vector = _vectorizer.transform([user_message])
    similarities = cosine_similarity(user_vector, _pattern_vectors)[0]

    best_idx = similarities.argmax()
    best_score = similarities[best_idx]
    best_tag = _pattern_tags[best_idx]

    if best_score < CONFIDENCE_THRESHOLD or best_tag not in _tag_to_intent:
        matched_tag = "fallback"
    else:
        matched_tag = best_tag

    intent = _tag_to_intent.get(
        matched_tag,
        {"responses": ["I'm not sure I understand yet. Teach me with /teach!"]},
    )
    response = random.choice(intent["responses"])

    return {
        "response": response,
        "matched_intent": matched_tag,
        "confidence": round(float(best_score), 3),
    }


def get_relevant_context(query: str, top_k: int = 3, min_score: float = 0.22) -> list:
    """
    Retrieval step for the AI models: find taught facts relevant to the
    query, to be injected into the system prompt as reference context
    (rather than returned directly as the final answer). This is what lets
    /teach still matter once responses come from a real language model --
    taught facts become grounding context the model can draw on, instead
    of being the literal reply.
    """
    if not _all_patterns:
        return []

    user_vector = _vectorizer.transform([query])
    similarities = cosine_similarity(user_vector, _pattern_vectors)[0]
    ranked_idx = similarities.argsort()[::-1]

    seen_tags = set()
    results = []
    for idx in ranked_idx:
        score = similarities[idx]
        if score < min_score:
            break
        tag = _pattern_tags[idx]
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        intent = _tag_to_intent.get(tag)
        if not intent or not intent.get("responses"):
            continue
        results.append({
            "question": _all_patterns[idx],
            "answer": intent["responses"][0],
        })
        if len(results) >= top_k:
            break
    return results


def teach(pattern: str, response: str, tag: str = None) -> dict:
    """Add a new (pattern -> response) example and retrain immediately."""
    with _lock:
        data = kv_store.get_json(LEARNED_STORE_KEY, default={"intents": []})
        intents = data["intents"]

        # Reuse an existing learned intent if the response text already exists,
        # otherwise create a new one.
        target = None
        for intent in intents:
            if response in intent["responses"]:
                target = intent
                break

        if target is None:
            new_tag = tag or f"learned_{len(intents) + 1}"
            target = {"tag": new_tag, "patterns": [], "responses": [response]}
            intents.append(target)

        target["patterns"].append(pattern)

        kv_store.set_json(LEARNED_STORE_KEY, data)

        train()

        return {"tag": target["tag"], "pattern": pattern, "response": response}


def list_learned(limit: int = 10) -> list:
    data = kv_store.get_json(LEARNED_STORE_KEY, default={"intents": []})
    flat = []
    for intent in data["intents"]:
        for i, pattern in enumerate(intent["patterns"]):
            response = intent["responses"][min(i, len(intent["responses"]) - 1)]
            flat.append({"tag": intent["tag"], "pattern": pattern, "response": response})
    return flat[-limit:]


def forget(index_from_end: int) -> bool:
    """Remove the Nth most recently taught example (1 = most recent)."""
    with _lock:
        data = kv_store.get_json(LEARNED_STORE_KEY, default={"intents": []})
        flat = []
        for intent in data["intents"]:
            for i in range(len(intent["patterns"])):
                flat.append((intent, i))

        if index_from_end < 1 or index_from_end > len(flat):
            return False

        intent, i = flat[-index_from_end]
        intent["patterns"].pop(i)
        if i < len(intent["responses"]):
            intent["responses"].pop(i) if len(intent["responses"]) > 1 else None

        data["intents"] = [it for it in data["intents"] if it["patterns"]]

        kv_store.set_json(LEARNED_STORE_KEY, data)

        train()
        return True


# Train once at import time.
train()
