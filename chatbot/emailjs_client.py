"""
Thin client for sending emails via EmailJS's REST API, used for 2FA login
codes. EmailJS is normally called from browser JS with a public key, but
it also supports server-side calls (POST /api/v1.0/email/send) -- adding
the private key (accessToken) is recommended for this since it prevents
the request being usable outside your configured origins.

Setup on EmailJS's dashboard (not something this code can do for you):
1. Add an email service (e.g. connect Gmail) -> gives you a Service ID.
2. Create an email template with variables like {{to_email}} and {{code}}
   -> gives you a Template ID. Important: the template's "To Email" field
   must be set to {{to_email}} (or whatever you name it), not a fixed
   address, or every code will go to the same inbox.
3. Copy your Public Key and Private Key from Account > API Keys.
"""

import requests

EMAILJS_API_URL = "https://api.emailjs.com/api/v1.0/email/send"
REQUEST_TIMEOUT = 20


class EmailConfigError(Exception):
    """Raised when EmailJS isn't configured yet (missing service/template/key)."""


class EmailRequestError(Exception):
    """Raised when the EmailJS API call itself fails."""


def send_email(email_settings: dict, to_email: str, code: str, app_name: str):
    service_id = (email_settings or {}).get("service_id")
    template_id = (email_settings or {}).get("template_id")
    public_key = (email_settings or {}).get("public_key")
    private_key = (email_settings or {}).get("private_key")

    if not service_id or not template_id or not public_key:
        raise EmailConfigError(
            "Email / 2FA isn't configured yet. An admin can set up EmailJS in the Settings tab."
        )

    payload = {
        "service_id": service_id,
        "template_id": template_id,
        "user_id": public_key,
        "template_params": {
            "to_email": to_email,
            "code": code,
            "app_name": app_name,
        },
    }
    if private_key:
        payload["accessToken"] = private_key

    try:
        resp = requests.post(
            EMAILJS_API_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise EmailRequestError(f"Couldn't reach EmailJS: {e}")

    if resp.status_code != 200:
        raise EmailRequestError(f"EmailJS error {resp.status_code}: {resp.text[:300]}")
