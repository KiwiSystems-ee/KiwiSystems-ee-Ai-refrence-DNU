"""
Entry point Vercel's Python runtime invokes. All the actual app logic
lives in app.py at the repo root, unchanged -- this file just makes it
importable from the api/ folder, which is the convention Vercel's builder
expects.

templates/ and static/ are still resolved relative to app.py's own
location (Flask(__name__) does this automatically), so nothing there
needs to change just because this entry point lives in a subfolder.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402  (Vercel's Python runtime looks for this WSGI `app` object)
