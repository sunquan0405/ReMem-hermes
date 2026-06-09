"""
LLM client - DeepSeek (OpenAI-compatible) API with Ollama fallback.
"""
import os
import logging

import httpx

logger = logging.getLogger("cognitive-worker.llm")

# ── DeepSeek (primary) ───────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ── Ollama (fallback) ────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-v4-flash:cloud")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")


async def deepseek_chat(prompt: str, model: str | None = None, timeout: int = 120) -> str:
    """
    Sends a prompt to DeepSeek API (OpenAI-compatible /v1/chat/completions).
    Uses DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL.
    """
    model = model or DEEPSEEK_MODEL
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    # Extract content from OpenAI-compatible response
    content = data["choices"][0]["message"]["content"]
    return content


async def llm_chat(prompt: str, model: str | None = None, timeout: int = 120) -> str:
    """
    Primary: DeepSeek API. Falls back to Ollama if DEEPSEEK_API_KEY is not set.
    """
    if DEEPSEEK_API_KEY:
        return await deepseek_chat(prompt, model=model, timeout=timeout)

    logger.warning("DEEPSEEK_API_KEY not set, falling back to Ollama")
    return await ollama_chat(prompt, model=model, timeout=timeout)


def get_auth_header() -> dict:
    """Returns auth header if API key is configured."""
    if OLLAMA_API_KEY:
        return {"Authorization": f"Bearer {OLLAMA_API_KEY}"}
    return {}


async def ollama_chat(prompt: str, model: str | None = None, timeout: int = 120) -> str:
    """
    Sends a prompt to native Ollama and returns the response.
    Uses cloud models like deepseek-v4-flash:cloud.
    """
    model = model or OLLAMA_MODEL
    url = f"{OLLAMA_BASE_URL}/api/generate"

    headers = {
        "Content-Type": "application/json",
        **get_auth_header(),
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 4096,  # DeepSeek generates long reasoning; needs space
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    # DeepSeek v4 flash: reasoning can consume tokens, leaving response empty
    # Return reasoning if content is empty
    response = data.get("response", "")
    if not response and "reasoning" in data:
        response = data["reasoning"]

    return response
