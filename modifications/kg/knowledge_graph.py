#!/usr/bin/env python3
"""Knowledge Graph — entity-relation query service for ReMem memory system.

Provides entity lookup, relation traversal, subgraph extraction, and
pre_llm_call context formatting. All data is in memory_store.db.

Usage:
    from services.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    kg.find_related("ai-platform")
    kg.get_subgraph(entity_id=1, depth=2)
"""

from __future__ import annotations

import os
import re
import sqlite3
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("workbench.kg")

# Default DB path: ~/.hermes/memory_store.db
DEFAULT_MEMORY_DB = Path.home() / ".hermes" / "memory_store.db"

# ── Standard relation types ──────────────────────────────────────────────
RELATION_TYPES = {
    "uses",           # X uses Y (technology, tool, library)
    "depends_on",     # X depends on Y
    "replaces",       # X replaces Y (migration, upgrade)
    "part_of",        # X is part of Y
    "mentioned_in",   # X is mentioned in fact Y
    "related_to",     # X is related to Y (soft link)
    "designed_by",    # X was designed by Y
    "implements",     # X implements Y (pattern, protocol)
}

# ── Entity types ─────────────────────────────────────────────────────────
ENTITY_TYPES = {
    "project",       # Software project / repo
    "technology",    # Technology, framework, library
    "tool",          # CLI tool, service, platform
    "company",       # Company / organization
    "person",        # Person
    "concept",       # Abstract concept
    "config",        # Configuration / setting
    "skill",         # Hermes skill
}


class KnowledgeGraph:
    """Knowledge Graph queries backed by memory_store.db."""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else DEFAULT_MEMORY_DB

    # ── Connection ───────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Entity lookup ────────────────────────────────────────────────────

    def get_entity(self, name: str) -> Optional[dict[str, Any]]:
        """Find an entity by exact name or alias match."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM entities WHERE name = ? OR aliases LIKE ?",
            (name, f"%{name}%"),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def search_entities(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fuzzy search entities by name."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM entities WHERE name LIKE ? OR aliases LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_entity_by_id(self, entity_id: int) -> Optional[dict[str, Any]]:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ── Relation queries ─────────────────────────────────────────────────

    def get_relations(
        self,
        entity_id: int,
        direction: str = "both",
        relation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all relations for an entity.

        Args:
            entity_id: The entity to query.
            direction: 'outgoing' (subject), 'incoming' (object), or 'both'.
            relation: Optional filter by relation type.
        """
        conn = self._conn()
        clauses = []
        params: list = []

        if direction in ("outgoing", "both"):
            clause = "subject_id = ?"
            params.append(entity_id)
            clauses.append(clause)

        if direction in ("incoming", "both"):
            clause = "object_id = ?"
            params.append(entity_id)
            clauses.append(clause)

        where = " OR ".join(clauses)
        if relation:
            where = f"({where}) AND relation = ?"
            params.append(relation)

        # Also check validity: valid_until IS NULL or valid_until > now
        where = f"({where}) AND (valid_until IS NULL OR valid_until >= datetime('now'))"

        rows = conn.execute(
            f"""SELECT er.*,
                       s.name AS subject_name, s.entity_type AS subject_type,
                       o.name AS object_name,  o.entity_type AS object_type
                FROM entity_relations er
                JOIN entities s ON er.subject_id = s.entity_id
                JOIN entities o ON er.object_id  = o.entity_id
                WHERE {where}
                ORDER BY er.confidence DESC
            """,
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def find_related(
        self,
        name: str,
        relation: str | None = None,
        max_depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Find entities related to a named entity by traversing relations.

        Args:
            name: Entity name (fuzzy matched).
            relation: Filter by relation type (e.g. 'uses', 'depends_on').
            max_depth: Graph traversal depth. 1 = direct relations only.

        Returns:
            List of relation dicts with subject/object names and types.
        """
        entity = self.get_entity(name)
        if not entity:
            return []

        eid = entity["entity_id"]
        all_relations = []
        visited = {eid}
        current_level = [eid]

        for _depth in range(max_depth):
            next_level = []
            for e in current_level:
                rels = self.get_relations(e, relation=relation)
                for r in rels:
                    all_relations.append(r)
                    # Collect neighbor IDs for next traversal
                    if r["subject_id"] not in visited:
                        visited.add(r["subject_id"])
                        next_level.append(r["subject_id"])
                    if r["object_id"] not in visited:
                        visited.add(r["object_id"])
                        next_level.append(r["object_id"])
            current_level = next_level
            if not current_level:
                break

        return all_relations

    def get_subgraph(self, entity_id: int, depth: int = 2) -> dict[str, Any]:
        """Get a subgraph centered on an entity (for LLM context injection)."""
        center = self.get_entity_by_id(entity_id)
        if not center:
            return {"center": None, "nodes": [], "edges": []}

        all_rels = self.find_related(center["name"], max_depth=depth)
        nodes: dict[int, dict] = {center["entity_id"]: center}
        edges: list[dict] = []

        for r in all_rels:
            sid, oid = r["subject_id"], r["object_id"]
            subj = self.get_entity_by_id(sid)
            obj = self.get_entity_by_id(oid)
            if subj and subj["entity_id"] not in nodes:
                nodes[subj["entity_id"]] = subj
            if obj and obj["entity_id"] not in nodes:
                nodes[obj["entity_id"]] = obj
            edges.append({
                "subject": subj["name"] if subj else "?",
                "relation": r["relation"],
                "object": obj["name"] if obj else "?",
                "confidence": r["confidence"],
            })

        return {
            "center": dict(center),
            "nodes": [dict(n) for n in nodes.values()],
            "edges": edges,
        }

    def get_entity_facts(self, entity_id: int) -> list[dict[str, Any]]:
        """Get all facts associated with an entity."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT f.* FROM facts f
               JOIN fact_entities fe ON f.fact_id = fe.fact_id
               WHERE fe.entity_id = ?
               ORDER BY f.updated_at DESC
               LIMIT 20
            """,
            (entity_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Pre_llm_call formatting ──────────────────────────────────────────

    def format_context(self, query: str, max_edges: int = 8) -> str:
        """Format KG context for pre_llm_call injection.

        Extracts potential entity names from the query, finds related entities,
        and returns a text block for injection.
        """
        # Entity extraction: match tokens and multi-word names
        q_lower = query.lower()
        q_tokens = {t for t in q_lower.split() if len(t) > 2}
        conn = self._conn()
        known = conn.execute(
            "SELECT name, entity_id, entity_type FROM entities"
        ).fetchall()
        conn.close()

        found = []
        for row in known:
            name = row["name"]
            n_lower = name.lower()
            # Match: exact substring, or all tokens of a multi-word name match
            if len(name) > 2:
                if n_lower in q_lower:
                    found.append((row["entity_id"], name, row["entity_type"]))
                elif len(n_lower.split()) > 1:
                    n_tokens = {t for t in n_lower.split() if len(t) > 2}
                    if n_tokens and n_tokens.issubset(q_tokens):
                        found.append((row["entity_id"], name, row["entity_type"]))

        if not found:
            return ""

        parts = []
        for eid, ename, etype in found:
            rels = self.get_relations(eid)
            if rels:
                lines = []
                for r in rels[:max_edges]:
                    lines.append(
                        f"  {r['subject_name']} --[{r['relation']}]--> {r['object_name']}"
                    )
                parts.append(f"[KG] {ename} ({etype}) 关联:\n" + "\n".join(lines))

        return "\n\n".join(parts) if parts else ""

    # ── Write operations ─────────────────────────────────────────────────

    def add_entity(
        self, name: str, entity_type: str = "unknown", aliases: str = ""
    ) -> int:
        """Add a new entity. Returns entity_id. No-op if name already exists."""
        conn = self._conn()
        existing = conn.execute(
            "SELECT entity_id FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            conn.close()
            return existing["entity_id"]
        cur = conn.execute(
            "INSERT INTO entities (name, entity_type, aliases) VALUES (?, ?, ?)",
            (name, entity_type, aliases),
        )
        eid = cur.lastrowid
        conn.commit()
        conn.close()
        logger.info("KG: added entity '%s' (id=%d, type=%s)", name, eid, entity_type)
        return eid

    def add_relation(
        self,
        subject: str,
        relation: str,
        object_: str,
        confidence: float = 0.5,
        source: str = "",
    ) -> bool:
        """Add a directed relation between two entities.

        Auto-creates entities if they don't exist.
        Skips if identical relation already exists.
        """
        sid = self.add_entity(subject)
        oid = self.add_entity(object_)

        conn = self._conn()
        existing = conn.execute(
            """SELECT relation_id FROM entity_relations
               WHERE subject_id = ? AND relation = ? AND object_id = ?
               AND (valid_until IS NULL OR valid_until >= datetime('now'))
            """,
            (sid, relation, oid),
        ).fetchone()
        if existing:
            conn.close()
            return False  # already exists

        conn.execute(
            """INSERT INTO entity_relations
               (subject_id, relation, object_id, confidence, source)
               VALUES (?, ?, ?, ?, ?)
            """,
            (sid, relation, oid, confidence, source),
        )
        conn.commit()
        conn.close()
        logger.info(
            "KG: added relation '%s --[%s]--> %s' (conf=%.1f)",
            subject, relation, object_, confidence,
        )
        return True


# ── CLI entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Graph CLI")
    parser.add_argument("action", choices=["search", "related", "subgraph", "add-entity", "add-relation"])
    parser.add_argument("args", nargs="*", help="Action arguments")
    args = parser.parse_args()

    kg = KnowledgeGraph()

    if args.action == "search":
        results = kg.search_entities(args.args[0] if args.args else "")
        for r in results:
            print(f"  #{r['entity_id']}  {r['name']}  ({r['entity_type']})")

    elif args.action == "related":
        name = args.args[0] if args.args else ""
        rels = kg.find_related(name)
        if not rels:
            print(f"No relations found for '{name}'")
        for r in rels:
            print(f"  {r['subject_name']} --[{r['relation']}]--> {r['object_name']}  (conf={r['confidence']})")

    elif args.action == "subgraph":
        name = args.args[0] if args.args else ""
        entity = kg.get_entity(name)
        if not entity:
            print(f"Entity '{name}' not found")
        else:
            sg = kg.get_subgraph(entity["entity_id"])
            print(f"Center: {sg['center']['name']} ({sg['center']['entity_type']})")
            print(f"Nodes ({len(sg['nodes'])}):")
            for n in sg["nodes"]:
                print(f"  #{n['entity_id']} {n['name']} ({n['entity_type']})")
            print(f"Edges ({len(sg['edges'])}):")
            for e in sg["edges"]:
                print(f"  {e['subject']} --[{e['relation']}]--> {e['object']}")

    elif args.action == "add-entity":
        name, etype = args.args[0], args.args[1] if len(args.args) > 1 else "unknown"
        eid = kg.add_entity(name, etype)
        print(f"Entity #{eid}: {name} ({etype}) {'(already existed)' if False else ''}")

    elif args.action == "add-relation":
        subj, rel, obj = args.args[0], args.args[1], args.args[2]
        ok = kg.add_relation(subj, rel, obj)
        print(f"Relation '{subj} --[{rel}]--> {obj}' {'added' if ok else 'already exists'}")
