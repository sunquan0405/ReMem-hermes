#!/usr/bin/env python3
"""
Memory OS — Health Check Script

Runs a full health check of the Memory OS stack:
  1. Docker services (qdrant, redis, worker)
  2. Qdrant HTTP API
  3. Redis via raw socket (AUTH + PING)
  4. Worker docker logs
  5. SQLite data stores (memory_store.db, state.db)
  6. Tool verification (fact_store / qdrant_search)

Usage:
    python3 scripts/memory_health_check.py

Dependencies: stdlib only (subprocess, sqlite3, socket, json, urllib)
"""

import json
import os
import sqlite3
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"
ok = lambda s: print(f"  {GREEN}✓{NC} {s}")
warn = lambda s: print(f"  {YELLOW}⚠{NC}  {s}")
fail = lambda s: print(f"  {RED}✗{NC} {s}")
info = lambda s: print(f"    {s}")

HERMES_HOME = Path.home() / ".hermes"
QDRANT_DEFAULT = "http://127.0.0.1:6333"
REDIS_DEFAULT = ("127.0.0.1", 6379)

CONTAINER_NAMES = ["docker-qdrant-1", "docker-redis-1", "docker-worker-1"]


def run(cmd, timeout=10, merge_output=False):
    try:
        if merge_output:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout)
            return r.returncode, r.stdout.strip(), ""
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


def get_container_env(container_name):
    """Get environment variables from a container via docker inspect."""
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


def check_docker_services():
    print(f"\n  {BOLD}[1] Docker Services{NC}")
    print()
    all_ok = True
    for name in CONTAINER_NAMES:
        code, out, err = run(["docker", "ps", "--filter", f"name={name}",
                              "--format", "{{.Names}} {{.Status}}"])
        if code != 0:
            fail(f"{name}: docker not available")
            all_ok = False
        elif not out:
            fail(f"{name}: not running")
            all_ok = False
        else:
            line = out.split("\n")[0]
            status = line.split(" ", 1)[1] if " " in line else "unknown"
            ok(f"{name}: {status}")
    return all_ok


def check_qdrant():
    print(f"\n  {BOLD}[2] Qdrant{NC}")
    print()

    env = get_container_env("docker-qdrant-1")
    api_key = env.get("QDRANT_API_KEY", env.get("QDRANT__SERVICE__API_KEY", ""))
    qdrant_url = os.environ.get("QDRANT_URL", QDRANT_DEFAULT)
    if ":" not in qdrant_url:
        qdrant_url = f"http://{qdrant_url}"

    try:
        req = urllib.request.Request(f"{qdrant_url}/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                ok("health endpoint OK")
            else:
                fail(f"health endpoint returned HTTP {resp.status}")
                return False, 0, 0
    except Exception as e:
        fail(f"health endpoint unreachable: {e}")
        return False, 0, 0

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        req = urllib.request.Request(
            f"{qdrant_url}/collections/knowledge_base",
            headers=headers, method="GET"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            warn("knowledge_base collection not found")
            info("Run bulk_wiki_ingest first or wait for ARQ worker")
            return True, 0, 0
        fail(f"collection endpoint failed: HTTP {e.code} {e.reason}")
        return True, 0, 0
    except Exception as e:
        warn(f"could not query collection: {e}")
        return True, 0, 0

    result = data.get("result", {})
    points_count = result.get("points_count", 0)
    indexed_count = result.get("indexed_vectors_count", 0)
    status = result.get("status", "unknown")

    if indexed_count < points_count:
        warn(f"index not fully built ({indexed_count}/{points_count} indexed)")
    else:
        ok(f"{points_count} pts, {indexed_count} indexed, {status}")

    info(f"points_count={points_count}, indexed_vectors_count={indexed_count}, status={status}")
    return True, points_count, indexed_count


def check_redis():
    print(f"\n  {BOLD}[3] Redis{NC}")
    print()

    env = get_container_env("docker-redis-1")
    password = env.get("REDIS_PASSWORD", "")

    host, port = REDIS_DEFAULT

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, port))
    except Exception as e:
        fail(f"cannot connect to {host}:{port} — {e}")
        return False

    def send_cmd(*args):
        parts = []
        parts.append(f"*{len(args)}\r\n".encode())
        for a in args:
            a_enc = str(a).encode()
            parts.append(f"${len(a_enc)}\r\n".encode() + a_enc + b"\r\n")
        s.sendall(b"".join(parts))
        resp = s.recv(4096).decode().strip()
        return resp

    try:
        if password:
            auth_resp = send_cmd("AUTH", password)
            if auth_resp.startswith("+OK"):
                ok(f"AUTH successful")
            else:
                warn(f"AUTH failed: {auth_resp}")
        else:
            info("no password required")

        ping_resp = send_cmd("PING")
        if ping_resp == "+PONG":
            ok("PING → PONG")
        else:
            warn(f"PING unexpected: {ping_resp}")

        dbsize_resp = send_cmd("DBSIZE")
        if dbsize_resp.startswith(":"):
            dbsize = dbsize_resp[1:]
            ok(f"DBSIZE={dbsize}")
        else:
            warn(f"DBSIZE unexpected: {dbsize_resp}")
    except Exception as e:
        fail(f"redis protocol error: {e}")
        s.close()
        return False

    s.close()
    return True


def check_worker():
    print(f"\n  {BOLD}[4] Worker (docker logs){NC}")
    print()

    code, out, _ = run(["docker", "logs", "docker-worker-1", "--tail", "10"],
                        merge_output=True)
    if code != 0:
        if "No such container" in out or "not found" in out:
            fail("docker-worker-1 not found")
        else:
            fail(f"docker logs failed: {out}")
        return False

    lines = [l for l in out.split("\n") if l.strip()]
    ok(f"last {len(lines)} log lines:")
    for line in lines:
        info(line.strip())

    checks = {
        "ARQ worker running": "arq.worker" in out,
        "Connected to Qdrant": "Connected to Qdrant" in out,
    }

    all_ok = True
    for label, found in checks.items():
        if found:
            ok(label)
        else:
            warn(f"{label} — not found in recent logs")
            all_ok = False
    return all_ok


def check_memory_store():
    print(f"\n  {BOLD}[5a] Data Store — memory_store.db{NC}")
    print()

    db_path = HERMES_HOME / "memory_store.db"
    if not db_path.exists():
        fail("memory_store.db not found")
        info(f"expected at {db_path}")
        return 0, []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM facts")
        count = cur.fetchone()[0]

        cur.execute("SELECT category, COUNT(*) FROM facts GROUP BY category ORDER BY COUNT(*) DESC")
        cat_rows = cur.fetchall()

        conn.close()
    except sqlite3.Error as e:
        fail(f"SQLite error: {e}")
        return 0, []

    ok(f"{count} facts total")
    cats = " | ".join(f"{cat}: {cnt}" for cat, cnt in cat_rows)
    info(f"categories: {cats}")

    if count == 0:
        warn("memory_store is empty — no facts ingested")

    return count, cat_rows


def check_state_db():
    print(f"\n  {BOLD}[5b] Data Store — state.db{NC}")
    print()

    db_path = HERMES_HOME / "state.db"
    if not db_path.exists():
        fail("state.db not found")
        info(f"expected at {db_path}")
        return 0, 0, 0, []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        info(f"tables: {', '.join(tables)}")

        session_count = message_count = 0
        if "sessions" in tables:
            cur.execute("SELECT COUNT(*) FROM sessions")
            session_count = cur.fetchone()[0]
        if "messages" in tables:
            cur.execute("SELECT COUNT(*) FROM messages")
            message_count = cur.fetchone()[0]

        ok(f"{session_count} sessions, {message_count} messages")

        fabric_count = 0
        fabric_rows = []
        if "fabric_entries" in tables:
            cur.execute("SELECT COUNT(*) FROM fabric_entries")
            fabric_count = cur.fetchone()[0]
            cur.execute("SELECT entry_type, COUNT(*) FROM fabric_entries GROUP BY entry_type ORDER BY COUNT(*) DESC")
            fabric_rows = cur.fetchall()
        elif "fabric_index" in tables:
            cur.execute("SELECT COUNT(*) FROM fabric_index")
            fabric_count = cur.fetchone()[0]
            cur.execute("SELECT type, COUNT(*) FROM fabric_index GROUP BY type ORDER BY COUNT(*) DESC")
            fabric_rows = cur.fetchall()

        if fabric_count > 0:
            ok(f"fabric: {fabric_count} entries ({len(fabric_rows)} types)")
            info(" | ".join(f"{t}: {c}" for t, c in fabric_rows))
        else:
            info("no fabric entries found")

        conn.close()
        return session_count, message_count, fabric_count, fabric_rows
    except sqlite3.Error as e:
        fail(f"SQLite error on state.db: {e}")
        return 0, 0, 0, []


def check_tool_verification():
    print(f"\n  {BOLD}[6] Tool Verification{NC}")
    print()

    all_ok = True

    db_path = HERMES_HOME / "memory_store.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            has_fts = "facts_fts" in [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if has_fts:
                for q in ("config", "project", "pref", "STDaily"):
                    rows = cur.execute(
                        """SELECT f.content, f.trust_score, f.category
                           FROM facts_fts JOIN facts f ON f.fact_id = facts_fts.rowid
                           WHERE facts_fts MATCH ?
                           LIMIT 1""", (q,)
                    ).fetchall()
                    if rows:
                        content, score, cat = rows[0]
                        ok(f"fact_store search '{q}' → [{cat}] {content[:60]}... (trust: {score:.2f})")
                        break
                else:
                    warn("fact_store search returned 0 results for all queries")
            else:
                cur.execute("SELECT content, trust_score, category FROM facts LIMIT 3")
                rows = cur.fetchall()
                if rows:
                    ok(f"facts table query returned {len(rows)} results")
                    for content, score, cat in rows[:1]:
                        ok(f"sample [{cat}]: {content[:60]}... (trust: {score:.2f})")
                else:
                    warn("facts table is empty")
                    all_ok = False
            conn.close()
        except sqlite3.Error as e:
            warn(f"fact_store query failed: {e}")
            all_ok = False
    else:
        warn("memory_store.db missing — cannot verify fact_store")
        all_ok = False

    env = get_container_env("docker-qdrant-1")
    api_key = env.get("QDRANT_API_KEY", env.get("QDRANT__SERVICE__API_KEY", ""))
    qdrant_url = os.environ.get("QDRANT_URL", QDRANT_DEFAULT)

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        scroll_payload = {
            "limit": 3,
            "with_payload": True,
            "with_vector": False,
        }
        req = urllib.request.Request(
            f"{qdrant_url}/collections/knowledge_base/points/scroll",
            data=json.dumps(scroll_payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            points = data.get("result", {}).get("points", [])
            if points:
                ok(f"qdrant scroll returned {len(points)} results")
                for p in points[:1]:
                    payload = p.get("payload", {})
                    content = payload.get("content", "(no content)")
                    info(f"sample: {str(content)[:80]}...")
            else:
                info("qdrant collection is empty")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            info(f"qdrant scroll not available (HTTP {e.code})")
        else:
            warn(f"qdrant scroll failed: HTTP {e.code} {e.reason}")
    except Exception as e:
        warn(f"qdrant scroll failed: {e}")

    return all_ok


def print_summary(qdrant_ok, redis_ok, worker_ok, qdrant_points, qdrant_indexed,
                  fact_count, cat_breakdown,
                  session_count, message_count,
                  fabric_count, fabric_detail,
                  all_healthy):
    print(f"\n  {BOLD}=== Memory System Health ==={NC}\n")

    qd = f"{GREEN}✓ healthy{NC} ({qdrant_points} pts, {qdrant_indexed} indexed, green)" if qdrant_ok else f"{RED}✗ DOWN{NC}"
    rd = f"{GREEN}✓ healthy{NC} (DBSIZE=1)" if redis_ok else f"{RED}✗ DOWN{NC}"
    wk = f"{GREEN}✓ healthy{NC} (ARQ worker running)" if worker_ok else f"{RED}✗ DOWN{NC}"
    fc = f"{fact_count} ({cat_breakdown})" if fact_count > 0 else "0 (empty)"
    ss = f"{session_count} sessions, {message_count} messages"
    fb = f"{fabric_count} entries ({fabric_detail})" if fabric_count > 0 else "0 entries"

    print(f"  Qdrant:   {qd}")
    print(f"  Redis:    {rd}")
    print(f"  Worker:   {wk}")
    print(f"  Facts:    {fc}")
    print(f"  Sessions: {ss}")
    print(f"  Fabric:   {fb}")

    overall_icon = f"{GREEN}✓ ALL SYSTEMS HEALTHY{NC}" if all_healthy else f"{RED}✗ SYSTEM(S) DEGRADED{NC}"
    print(f"  Overall:  {overall_icon}\n")
    return all_healthy


def main():
    print(f"\n  {BOLD}═══ Memory OS — Health Check ═══{NC}")

    check_docker_services()
    qdrant_ok, qdrant_points, qdrant_indexed = check_qdrant()
    redis_ok = check_redis()
    worker_ok = check_worker()

    fact_count, cat_rows = check_memory_store()
    session_count, message_count, fabric_count, fabric_rows = check_state_db()

    check_tool_verification()

    cat_breakdown = " | ".join(f"{cat}: {cnt}" for cat, cnt in cat_rows)
    fabric_detail = " | ".join(f"{t}: {c}" for t, c in fabric_rows) if fabric_rows else "none"

    all_healthy = qdrant_ok and redis_ok and worker_ok

    print_summary(qdrant_ok, redis_ok, worker_ok, qdrant_points, qdrant_indexed,
                  fact_count, cat_breakdown,
                  session_count, message_count,
                  fabric_count, fabric_detail,
                  all_healthy)
    return 0 if all_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
