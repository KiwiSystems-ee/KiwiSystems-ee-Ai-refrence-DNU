import os
import secrets
import random

from flask import Flask, request, jsonify, render_template, session, redirect, url_for

import chatbot_engine as brain
import accounts
import ai_settings
import nvidia_client
import emailjs_client
import kv_store
from nvidia_client import AIConfigError, AIRequestError
from emailjs_client import EmailConfigError, EmailRequestError

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))

BRAND_NAME = "KiwiSystems"
BOT_NAME = "KiwiSystems AI"
COPYRIGHT_YEAR = "2026"

# Pending 2FA codes live in the KV store (not an in-memory dict): on a
# serverless host, the request that sends the code and the request that
# verifies it can land in two completely different containers, so an
# in-memory dict wouldn't be visible to both. Redis's own TTL (via
# ex_seconds) handles expiry automatically. Purpose is "login" or
# "admin_login" so a code from one flow can't complete the other.
CODE_TTL_SECONDS = 5 * 60

# Pre-create these accounts on first run so they can log in immediately.
# Both are granted admin rights, so they can log into /admin with these
# same credentials and manage other users from there.
# Wrapped: if storage (Upstash) isn't configured yet, the app should still
# start and serve pages -- login just won't work until it's set up, rather
# than the whole app crashing on import/cold-start.
try:
    accounts.seed_if_empty([
        {"username": "tima", "email": "", "password": "93497190028944@lUser1Own", "is_admin": True},
        {"username": "Hbelbs", "email": "", "password": "82838281737128@User2coown", "is_admin": True},
    ])
except (kv_store.KVConfigError, kv_store.KVRequestError) as e:
    print(f"WARNING: couldn't seed initial accounts (storage not ready?): {e}")


def _client_ip() -> str:
    # If this ever runs behind a reverse proxy, prefer X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _template_ctx(**extra):
    return {"brand": BRAND_NAME, "bot_name": BOT_NAME, "copyright_year": COPYRIGHT_YEAR, **extra}


def _pending_2fa_key(username: str) -> str:
    return f"2fa:{username.strip().lower()}"


def _start_2fa(username: str, email_addr: str, purpose: str) -> dict:
    """Generate a code, email it, and stash it as pending. Returns a JSON-safe result dict."""
    code = f"{random.randint(0, 999999):06d}"
    try:
        emailjs_client.send_email(
            ai_settings.get_email_2fa(),
            email_addr,
            code,
            BOT_NAME,
        )
    except (EmailConfigError, EmailRequestError) as e:
        return {"ok": False, "error": str(e)}

    try:
        kv_store.set_json(
            _pending_2fa_key(username),
            {"code": code, "purpose": purpose},
            ex_seconds=CODE_TTL_SECONDS,
        )
    except (kv_store.KVConfigError, kv_store.KVRequestError) as e:
        return {"ok": False, "error": f"Couldn't store the verification code: {e}"}

    return {"ok": True, "requires_2fa": True}


def _verify_2fa(username: str, code: str, purpose: str) -> bool:
    try:
        entry = kv_store.get_json(_pending_2fa_key(username), default=None)
    except (kv_store.KVConfigError, kv_store.KVRequestError):
        return False
    if not entry:
        return False
    if entry.get("purpose") != purpose:
        return False
    if entry.get("code") != code.strip():
        return False
    kv_store.delete(_pending_2fa_key(username))
    return True


# ---------------------------------------------------------------- pages ----

@app.route("/")
def index():
    if not session.get("username"):
        return redirect(url_for("login_page"))
    return render_template("index.html", **_template_ctx(username=session.get("username")))


@app.route("/signup")
def signup_page():
    return render_template("signup.html", **_template_ctx())


@app.route("/login")
def login_page():
    return render_template("login.html", **_template_ctx())


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login_page"))


@app.route("/admin")
def admin_page():
    return render_template("admin.html", **_template_ctx())


@app.route("/terms")
def terms_page():
    return render_template("terms.html", **_template_ctx())


@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html", **_template_ctx())


# ---------------------------------------------------------- account api ----

@app.route("/api/signup", methods=["POST"])
def api_signup():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password are required"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400

    try:
        accounts.create_user(username, email, password, _client_ip())
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    session["username"] = username
    return jsonify({"ok": True})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not accounts.verify_login(username, password):
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401

    user = accounts.get_user(username)
    email_addr = (user or {}).get("email")

    if not email_addr:
        session["username"] = username
        return jsonify({"ok": True, "requires_2fa": False})

    result = _start_2fa(username, email_addr, "login")
    if not result.get("ok"):
        # Email failed to send -- don't lock the person out, log them straight in
        # and surface the problem so an admin can fix Settings.
        session["username"] = username
        return jsonify({"ok": True, "requires_2fa": False, "email_warning": result.get("error")})
    return jsonify(result)


@app.route("/api/login/verify-2fa", methods=["POST"])
def api_login_verify_2fa():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    code = (data.get("code") or "").strip()

    if _verify_2fa(username, code, "login"):
        session["username"] = username
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid or expired code"}), 401


@app.route("/api/login/resend-2fa", methods=["POST"])
def api_login_resend_2fa():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    user = accounts.get_user(username)
    email_addr = (user or {}).get("email")
    if not email_addr:
        return jsonify({"ok": False, "error": "No email on file"}), 400
    result = _start_2fa(username, email_addr, "login")
    return jsonify(result)


# ------------------------------------------------------------ public api ---

VALID_MODES = {"general", "coding", "image"}


def _build_system_prompt(mode: str, user_message: str) -> str:
    persona = f"You are {BOT_NAME}, a helpful AI assistant for {BRAND_NAME}. Be clear, direct, and friendly."
    if mode == "coding":
        persona += " Focus on correct, working code. Use fenced code blocks for any code, and briefly explain non-obvious parts."

    parts = [persona]

    rules = ai_settings.get_rules()
    if rules:
        rules_block = "\n".join(f"- {r}" for r in rules)
        parts.append(f"Additional rules you must always follow:\n{rules_block}")

    context = brain.get_relevant_context(user_message)
    if context:
        context_block = "\n".join(f"- Q: {c['question']}\n  A: {c['answer']}" for c in context)
        parts.append(
            "Reference information that may be relevant to this question "
            "(only use it if it actually applies; otherwise ignore it):\n" + context_block
        )

    return "\n\n".join(parts)


@app.route("/api/chat", methods=["POST"])
def chat():
    if not session.get("username"):
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    user_message = (data.get("message") or "").strip()
    mode = data.get("mode") or "general"
    if mode not in VALID_MODES:
        mode = "general"
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    if mode == "image":
        try:
            b64 = nvidia_client.generate_image(ai_settings.get_category("image"), user_message)
            return jsonify({"image_b64": b64, "mode": mode})
        except AIConfigError as e:
            return jsonify({"response": str(e), "mode": mode, "config_error": True})
        except AIRequestError as e:
            return jsonify({"response": f"Image generation failed: {e}", "mode": mode, "request_error": True})

    system_prompt = _build_system_prompt(mode, user_message)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        reply_text = nvidia_client.chat_completion(ai_settings.get_category(mode), messages)
        return jsonify({"response": reply_text, "mode": mode})
    except AIConfigError as e:
        return jsonify({"response": str(e), "mode": mode, "config_error": True})
    except AIRequestError as e:
        return jsonify({"response": f"Something went wrong talking to the AI: {e}", "mode": mode, "request_error": True})


# ------------------------------------------------------------- admin api ---

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not accounts.verify_login(username, password):
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    if not accounts.is_admin(username):
        return jsonify({"ok": False, "error": "This account doesn't have admin access"}), 403

    user = accounts.get_user(username)
    email_addr = (user or {}).get("email")

    def _complete_admin_login():
        session["is_admin"] = True
        session["admin_username"] = username
        session["teach_mode"] = False
        session["username"] = username

    if not email_addr:
        _complete_admin_login()
        return jsonify({"ok": True, "requires_2fa": False})

    result = _start_2fa(username, email_addr, "admin_login")
    if not result.get("ok"):
        _complete_admin_login()
        return jsonify({"ok": True, "requires_2fa": False, "email_warning": result.get("error")})
    return jsonify(result)


@app.route("/api/admin/login/verify-2fa", methods=["POST"])
def admin_login_verify_2fa():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    code = (data.get("code") or "").strip()

    if not _verify_2fa(username, code, "admin_login"):
        return jsonify({"ok": False, "error": "Invalid or expired code"}), 401

    session["is_admin"] = True
    session["admin_username"] = username
    session["teach_mode"] = False
    session["username"] = username
    return jsonify({"ok": True})


@app.route("/api/admin/login/resend-2fa", methods=["POST"])
def admin_login_resend_2fa():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    user = accounts.get_user(username)
    email_addr = (user or {}).get("email")
    if not email_addr:
        return jsonify({"ok": False, "error": "No email on file"}), 400
    result = _start_2fa(username, email_addr, "admin_login")
    return jsonify(result)


@app.route("/api/admin/status")
def admin_status():
    return jsonify({
        "logged_in": bool(session.get("is_admin")),
        "teach_mode": bool(session.get("teach_mode")),
        "admin_username": session.get("admin_username"),
    })


def _require_admin():
    return bool(session.get("is_admin"))


@app.route("/api/admin/users", methods=["GET"])
def admin_list_users():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401
    return jsonify({"users": accounts.list_users()})


@app.route("/api/admin/users", methods=["POST"])
def admin_create_user():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    make_admin = bool(data.get("is_admin", False))

    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password are required"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400

    try:
        user = accounts.create_user(username, email, password, "added-by-admin", is_admin=make_admin)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    return jsonify({"ok": True, "user": user})


@app.route("/api/admin/users/set-email", methods=["POST"])
def admin_set_email():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()

    if not username:
        return jsonify({"ok": False, "error": "Username is required"}), 400

    ok = accounts.set_email(username, email)
    if not ok:
        return jsonify({"ok": False, "error": "No such user"}), 404
    return jsonify({"ok": True, "email": email})


@app.route("/api/admin/users/reset-password", methods=["POST"])
def admin_reset_password():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    new_password = data.get("new_password") or ""

    if not username or not new_password:
        return jsonify({"ok": False, "error": "Username and new password are required"}), 400
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "Password must be at least 6 characters"}), 400

    ok = accounts.set_password(username, new_password)
    if not ok:
        return jsonify({"ok": False, "error": "No such user"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["GET"])
def admin_get_settings():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401
    return jsonify(ai_settings.get_settings())


@app.route("/api/admin/settings", methods=["POST"])
def admin_update_settings():
    """
    Update one category's API key / model (/ endpoint for image), or the
    EmailJS 2FA config. Body:
    { "category": "general" | "coding" | "image" | "email_2fa", ... }
    Any field omitted is left unchanged.
    """
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    category = data.get("category")

    if category == "email_2fa":
        updated = ai_settings.update_email_2fa(
            service_id=data.get("service_id"),
            template_id=data.get("template_id"),
            public_key=data.get("public_key"),
            private_key=data.get("private_key"),
        )
        return jsonify({"ok": True, "category": "email_2fa", "settings": updated})

    if category not in ("general", "coding", "image"):
        return jsonify({"ok": False, "error": "category must be general, coding, image, or email_2fa"}), 400

    updated = ai_settings.update_category(
        category,
        api_key=data.get("api_key"),
        model=data.get("model"),
        endpoint=data.get("endpoint"),
    )
    return jsonify({"ok": True, "category": category, "settings": updated})


@app.route("/api/admin/teach-code", methods=["POST"])
def admin_teach_code():
    """
    Teach a multi-line answer (e.g. a code snippet) that preserves exact
    formatting and indentation. Separate from the single-line "q => a" chat
    syntax, since a plain text input can't hold newlines -- this is meant
    to be called from a <textarea>-based form instead.
    """
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    patterns_raw = data.get("patterns") or ""
    answer = data.get("answer") or ""

    patterns = [p.strip() for p in patterns_raw.split("|") if p.strip()]
    answer = answer.strip("\n")  # trim leading/trailing blank lines, keep internal formatting

    if not patterns or not answer:
        return jsonify({"ok": False, "error": "At least one question and a non-empty answer are required"}), 400

    taught = []
    for pattern in patterns:
        result = brain.teach(pattern, answer)
        taught.append(result["pattern"])

    return jsonify({"ok": True, "patterns": taught, "answer": answer})


@app.route("/api/admin/chat", methods=["POST"])
def admin_chat():
    if not _require_admin():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400

    # --- commands -----------------------------------------------------
    if message == "/teach":
        session["teach_mode"] = True
        return jsonify({
            "system": True,
            "response": (
                "Teaching mode ON. Send lines like:\n"
                "  how do I reset my password => Go to Settings > Security > Reset\n"
                "Teach several phrasings at once with a similar meaning by separating them with | :\n"
                "  forgot my password | reset my password | i forgot my password => Go to Settings > Security > Reset\n"
                "They'll all trigger the same answer.\n"
                "Or send a normal message, then correct my reply with:\n"
                "  /correct <the response I should have given>\n"
                "Send /unteach when you're done."
            ),
            "teach_mode": True,
        })

    if message == "/unteach":
        session["teach_mode"] = False
        return jsonify({"system": True, "response": "Teaching mode OFF. Back to normal chat.", "teach_mode": False})

    if message == "/list":
        recent = brain.list_learned(10)
        if not recent:
            text = "Nothing taught yet."
        else:
            lines = [f"{i+1}. \"{e['pattern']}\" -> \"{e['response']}\" [{e['tag']}]" for i, e in enumerate(reversed(recent))]
            text = "Most recently taught (1 = newest):\n" + "\n".join(lines)
        return jsonify({"system": True, "response": text, "teach_mode": bool(session.get("teach_mode"))})

    if message.startswith("/forget"):
        parts = message.split()
        try:
            idx = int(parts[1]) if len(parts) > 1 else 1
        except ValueError:
            idx = 1
        ok = brain.forget(idx)
        text = f"Removed taught example #{idx}." if ok else f"No taught example at #{idx}."
        return jsonify({"system": True, "response": text, "teach_mode": bool(session.get("teach_mode"))})

    if message.startswith("/correct"):
        correction = message[len("/correct"):].strip()
        last_msg = session.get("last_user_message")
        if not last_msg:
            return jsonify({"system": True, "response": "Nothing to correct yet -- send a message first.", "teach_mode": bool(session.get("teach_mode"))})
        if not correction:
            return jsonify({"system": True, "response": "Usage: /correct <the response I should have given>", "teach_mode": bool(session.get("teach_mode"))})
        result = brain.teach(last_msg, correction)
        return jsonify({
            "system": True,
            "response": f"Got it -- learned: \"{result['pattern']}\" -> \"{result['response']}\"",
            "teach_mode": bool(session.get("teach_mode")),
        })

    if message.startswith("/rule "):
        rule_text = message[len("/rule "):].strip()
        if not rule_text:
            return jsonify({"system": True, "response": "Usage: /rule <an instruction for how the AI should speak or behave>", "teach_mode": bool(session.get("teach_mode"))})
        rules = ai_settings.add_rule(rule_text)
        return jsonify({
            "system": True,
            "response": f"Rule added ({len(rules)} total): \"{rule_text}\"",
            "teach_mode": bool(session.get("teach_mode")),
        })

    if message == "/rules":
        rules = ai_settings.get_rules()
        if not rules:
            text = "No rules set yet. Add one with /rule <instruction>."
        else:
            lines = [f"{i+1}. {r}" for i, r in enumerate(reversed(rules))]
            text = "Active rules (1 = most recently added):\n" + "\n".join(lines)
        return jsonify({"system": True, "response": text, "teach_mode": bool(session.get("teach_mode"))})

    if message.startswith("/unrule"):
        parts = message.split()
        try:
            idx = int(parts[1]) if len(parts) > 1 else 1
        except ValueError:
            idx = 1
        ok = ai_settings.remove_rule(idx)
        text = f"Removed rule #{idx}." if ok else f"No rule at #{idx}."
        return jsonify({"system": True, "response": text, "teach_mode": bool(session.get("teach_mode"))})

    # --- teaching mode: plain "pattern => response" lines --------------
    if session.get("teach_mode"):
        if "=>" in message:
            pattern_part, response = message.split("=>", 1)
            response = response.strip()
            # Multiple phrasings can be taught at once, separated by "|",
            # e.g. "forgot my password | reset my password => Go to Settings..."
            # They all map to the same response and are grouped under one topic.
            patterns = [p.strip() for p in pattern_part.split("|") if p.strip()]

            if not patterns or not response:
                return jsonify({"system": True, "response": "Format: question => answer  (use | to add more phrasings, e.g. forgot my password | reset my password => answer)", "teach_mode": True})

            taught = []
            for pattern in patterns:
                result = brain.teach(pattern, response)
                taught.append(result["pattern"])

            if len(taught) == 1:
                summary = f"Learned: \"{taught[0]}\" -> \"{response}\""
            else:
                phrasing_list = "\n".join(f"  - \"{p}\"" for p in taught)
                summary = f"Learned {len(taught)} phrasings, all answered with \"{response}\":\n{phrasing_list}"

            return jsonify({
                "system": True,
                "response": summary,
                "teach_mode": True,
            })
        else:
            session["last_user_message"] = message
            reply = brain.get_response(message)
            reply["teach_mode"] = True
            reply["hint"] = "In teach mode: 'question => answer' (or 'q1 | q2 | q3 => answer' for several phrasings), or /correct <better answer> to fix this reply."
            return jsonify(reply)

    # --- normal chat, remember for potential /correct -------------------
    session["last_user_message"] = message
    reply = brain.get_response(message)
    reply["teach_mode"] = False
    return jsonify(reply)


if __name__ == "__main__":
    # use_reloader=False: the reloader watches all files in this folder,
    # and teaching/signup writes JSON files -- that would trigger an
    # auto-restart mid-request every time someone teaches or signs up.
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=5000)
