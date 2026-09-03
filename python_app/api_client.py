"""Small compatibility helpers for OpenAI-style Chat Completions APIs."""
from __future__ import annotations

from urllib.error import HTTPError, URLError


def chat_completions_url(endpoint: str) -> str:
    """Accept either a full Chat Completions URL or the common `/v1` base URL."""
    url = (endpoint or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def readable_api_error(error: Exception) -> str:
    if isinstance(error, HTTPError):
        try:
            detail = error.read(600).decode("utf-8", errors="replace").replace("\n", " ")
        except OSError:
            detail = ""
        return f"接口返回 HTTP {error.code}" + (f"：{detail}" if detail else "")
    if isinstance(error, URLError):
        return f"无法连接接口：{error.reason}"
    return str(error) or error.__class__.__name__
