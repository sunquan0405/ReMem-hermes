#!/usr/bin/env python3
"""
Memory OS — Embedding API Connectivity Test

Tests the embedding API configuration and reports any misconfigurations:
  1. Read config from environment
  2. Validate model name (warn if DeepSeek model)
  3. Test API call to /embeddings
  4. Compare dimensions with Qdrant collection

Usage:
    python3 scripts/test_embedding_api.py

Dependencies: stdlib only (os, json, urllib.request, time)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"

QDRANT_DEFAULT = "http://127.0.0.1:6333"


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


def get_container_env(container_name):
    code, out, err = run(["docker", "inspect", container_name,
                          "--format", "{{json .Config.Env}}"])
    if code != 0:
        return {}
    try:
        env_list = json.loads(out)
        env_dict = {}
        for entry in env_list:
            if "=" in entry:
                k, v = entry.split("=", 1)
                env_dict[k] = v
        return env_dict
    except (json.JSONDecodeError, ValueError):
        return {}


def get_qdrant_vector_size():
    """Query Qdrant collection config and return vector dimension."""
    env = get_container_env("docker-qdrant-1")
    api_key = env.get("QDRANT_API_KEY", env.get("QDRANT__SERVICE__API_KEY", ""))
    qdrant_url = os.environ.get("QDRANT_URL", QDRANT_DEFAULT)
    if ":" not in qdrant_url:
        qdrant_url = f"http://{qdrant_url}"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    try:
        req = urllib.request.Request(
            f"{qdrant_url}/collections/knowledge_base",
            headers=headers, method="GET"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None

    result = data.get("result", {})
    vectors_config = result.get("config", {}).get("params", {}).get("vectors", {})
    if isinstance(vectors_config, dict):
        for v in vectors_config.values():
            if isinstance(v, dict) and "size" in v:
                return v["size"]
    return None


def main():
    model = os.environ.get("EMBEDDING_MODEL", "")
    api_key = os.environ.get("EMBEDDING_API_KEY", "")
    base_url = os.environ.get("EMBEDDING_BASE_URL", "")

    print(f"\n  {BOLD}=== Embedding API Test ==={NC}\n")

    fmt = lambda label, val: f"  {label:<9} {val}"

    print(fmt("Model:", model if model else f"{RED}(not set){NC}"))
    print(fmt("Base URL:", base_url if base_url else f"{RED}(not set){NC}"))

    if not model or not base_url or not api_key:
        if not model:
            print(f"  {RED}✗{NC} EMBEDDING_MODEL not set")
        if not base_url:
            print(f"  {RED}✗{NC} EMBEDDING_BASE_URL not set")
        if not api_key:
            print(f"  {RED}✗{NC} EMBEDDING_API_KEY not set")
        info = lambda s: print(f"    {s}")
        info("Set these in ~/.hermes/.env or export them.")
        print()
        return 1

    misconfigured = False
    dims = None
    status_text = "N/A"
    response_time = None

    if model.startswith("deepseek"):
        print(fmt("Status:", f"{RED}✗ FAILED (skipped){NC}"))
        print(fmt("Dims:", "N/A"))
        print(f"  {YELLOW}⚠{NC}  WARNING: {model} is a chat model, not an embedding model!")
        print("    DeepSeek has no embedding models.")
        print("    Correct models: text-embedding-v4 (Qwen, 1024-dim)")
        print("                  or qwen/qwen3-embedding-8b (OpenRouter, 4096-dim)")
        misconfigured = True
    else:
        base = base_url.rstrip("/")
        candidates = [f"{base}/v1/embeddings"]
        if not base.endswith("/v1"):
            candidates.append(f"{base}/embeddings")

        payload = json.dumps({
            "model": model,
            "input": "test embedding connectivity",
        }).encode()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        for attempt_url in candidates:
            try:
                start = time.time()
                req = urllib.request.Request(attempt_url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    response_time = int((time.time() - start) * 1000)
                    body = json.loads(resp.read().decode())
                    status_text = f"{resp.status}, {response_time}ms"

                    embedding_data = body.get("data", [])
                    if embedding_data:
                        dims = len(embedding_data[0].get("embedding", []))
                    break
            except urllib.error.HTTPError as e:
                if e.code == 404 and len(candidates) > 1:
                    continue
                status_text = f"{e.code} {e.reason}"
                body_text = e.read().decode()
                if body_text:
                    status_text += f" — {body_text[:120]}"
                break
            except urllib.error.URLError as e:
                status_text = f"CONNECTION FAILED ({e.reason})"
                break
            except Exception as e:
                status_text = f"ERROR ({e})"
                break
        else:
            status_text = "404 (both endpoints)"

        if dims is not None:
            print(fmt("Status:", f"{GREEN}✓ Connected{NC} ({status_text})"))
            print(fmt("Dims:", str(dims)))
        else:
            print(fmt("Status:", f"{RED}✗ {status_text}{NC}"))
            print(fmt("Dims:", "N/A"))

    qdrant_dim = get_qdrant_vector_size()
    if qdrant_dim is not None:
        if dims is not None:
            match = dims == qdrant_dim
            arrow = f"{GREEN}✓ Match{NC}" if match else f"{RED}✗ Mismatch{NC}"
            print(fmt("Qdrant:", f"{qdrant_dim}-dim collection → {arrow}"))
            if not match:
                print(f"  {YELLOW}⚠{NC}  API={dims} vs Qdrant={qdrant_dim} — vectors will be incompatible!")
                misconfigured = True
        else:
            print(fmt("Qdrant:", f"{qdrant_dim}-dim collection"))
    else:
        print(fmt("Qdrant:", f"{YELLOW}(unreachable){NC}"))

    if model.startswith("deepseek"):
        print(f"\n  Verdict:  {RED}✗ MISCONFIGURED{NC} — fix EMBEDDING_MODEL in ~/.hermes/.env")
        print()
        return 1

    if dims is not None and qdrant_dim is not None and dims != qdrant_dim:
        print(f"\n  Verdict:  {RED}✗ MISCONFIGURED{NC} — dimension mismatch")
        print()
        return 1

    if dims is None:
        print(f"\n  Verdict:  {RED}✗ FAILED{NC} — could not connect to embedding API")
        print()
        return 1

    print(f"\n  Verdict:  {GREEN}✓ CONFIGURATION CORRECT{NC}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
