#!/usr/bin/env python3
"""
KG backfill — scan existing facts + fabric entries to build initial
entity_relations table from historical data.

Usage:
    python3 scripts/backfill_kg.py              # full backfill
    python3 scripts/backfill_kg.py --dry-run     # preview only
    python3 scripts/backfill_kg.py --facts-only  # only facts
    python3 scripts/backfill_kg.py --fabric-only # only fabric
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.knowledge_graph import KnowledgeGraph

# ── Config ───────────────────────────────────────────────────────────────
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
MEMORY_DB = DEFAULT_HERMES_HOME / "memory_store.db"
STATE_DB = DEFAULT_HERMES_HOME / "state.db"
HERMES_MEMORY_DIR = DEFAULT_HERMES_HOME / "memory"

# Explicit relation patterns found in fact content
RELATION_PATTERNS = [
    (r'\buses?\s+(\w[\w.-]+)', 'uses'),
    (r'\bdepends?\s+on\s+(\w[\w.-]+)', 'depends_on'),
    (r'\breplac(?:es|ed)\s+(\w[\w.-]+)', 'replaces'),
    (r'\bpart\s+of\s+(\w[\w.-]+)', 'part_of'),
    (r'\bbuilt\s+(?:with|on)\s+(\w[\w.-]+)', 'uses'),
    (r'\bpowered\s+by\s+(\w[\w.-]+)', 'uses'),
    (r'\brunning\s+on\s+(\w[\w.-]+)', 'depends_on'),
    (r'\bdeployed\s+(?:on|via)\s+(\w[\w.-]+)', 'depends_on'),
    (r'\bintegrat(?:es|ed)\s+(?:with|into)\s+(\w[\w.-]+)', 'related_to'),
    (r'\bmigrat(?:es?|ed)\s+(?:from\s+)?(\w[\w.-]+)(?:\s+to\s+(\w[\w.-]+))?', 'replaces'),
]


def backfill_from_facts(kg: KnowledgeGraph, dry_run: bool = False) -> dict:
    """Scan existing facts for entity mentions and relation patterns."""
    stats = {"facts_scanned": 0, "entities_found": 0, "relations_found": 0, "patterns_matched": 0}

    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row

    facts = conn.execute("SELECT fact_id, content, category, tags FROM facts ORDER BY fact_id").fetchall()
    stats["facts_scanned"] = len(facts)

    for fact in facts:
        content = fact["content"]
        fid = fact["fact_id"]

        # Extract potential entity names: PascalCase words, quoted terms, known patterns
        # Simple heuristic: find capitalized multi-word terms and common tech names
        candidates = set()
        # Pattern: "Xxx Yyy" or "XxxYyy" (PascalCase)
        for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', content):
            word = m.group(1).strip()
            if 3 <= len(word) <= 40 and word != "The":
                candidates.add(word)
        # Pattern: lowercase-kebab or snake_case (tool/package names)
        for m in re.finditer(r'\b([a-z][a-z0-9]+(?:[-_][a-z0-9]+)+)\b', content):
            word = m.group(1)
            if 4 <= len(word) <= 30:
                candidates.add(word)

        for name in candidates:
            if not dry_run:
                kg.add_entity(name)
            stats["entities_found"] += 1

        # Extract relations from content patterns
        for pattern, rel_type in RELATION_PATTERNS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                groups = m.groups()
                subject_candidates = [c for c in candidates if c.lower() in content[:m.start()].lower()]
                if not subject_candidates:
                    continue
                # Use the last candidate before the match as subject
                subject = max(subject_candidates, key=lambda x: len(x))
                obj = groups[0]
                if len(obj) >= 2:
                    if not dry_run:
                        kg.add_entity(obj)
                        kg.add_relation(subject, rel_type, obj, source=f"backfill:fact:{fid}")
                    stats["relations_found"] += 1
                    stats["patterns_matched"] += 1

    conn.close()
    return stats


def backfill_from_existing_associations(kg: KnowledgeGraph, dry_run: bool = False) -> dict:
    """Convert fact_entities junction table entries into explicit relations."""
    stats = {"associations_scanned": 0, "relations_added": 0}

    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row

    # For each fact that has multiple entities, link them as "mentioned_in"
    assoc = conn.execute(
        """SELECT fe.fact_id, fe.entity_id, e.name, f.content
           FROM fact_entities fe
           JOIN entities e ON fe.entity_id = e.entity_id
           JOIN facts f ON fe.fact_id = f.fact_id
           ORDER BY fe.fact_id"""
    ).fetchall()

    stats["associations_scanned"] = len(assoc)

    # Group by fact_id
    fact_groups: dict[int, list] = {}
    for row in assoc:
        fid = row["fact_id"]
        if fid not in fact_groups:
            fact_groups[fid] = []
        fact_groups[fid].append({"entity_id": row["entity_id"], "name": row["name"]})

    for fid, entities in fact_groups.items():
        if len(entities) < 2:
            continue
        # Link all entities in the same fact with "mentioned_in"
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                if not dry_run:
                    kg.add_relation(
                        entities[i]["name"],
                        "mentioned_in",
                        entities[j]["name"],
                        confidence=0.5,
                        source=f"backfill:fact_entities:{fid}",
                    )
                    stats["relations_added"] += 1

    conn.close()
    return stats


def backfill_from_fabric(kg: KnowledgeGraph, dry_run: bool = False) -> dict:
    """Scan fabric entries for entity names."""
    stats = {"entries_scanned": 0, "entities_found": 0}

    conn = sqlite3.connect(str(STATE_DB))
    conn.row_factory = sqlite3.Row

    try:
        entries = conn.execute(
            "SELECT id, summary, content, agent FROM fabric_index ORDER BY id LIMIT 200"
        ).fetchall()
    except sqlite3.OperationalError:
        print("  (no fabric_index table in state.db — skipping)")
        conn.close()
        return stats

    stats["entries_scanned"] = len(entries)

    for entry in entries:
        text = f"{entry['summary'] or ''} {entry['content'] or ''}"
        if not text.strip():
            continue
        # Find PascalCase names
        for m in re.finditer(r'\b([A-Z][a-z]+(?:[-/\s][A-Z][a-z]+)*)\b', text):
            word = m.group(1).strip()
            if 3 <= len(word) <= 40 and word != "The":
                if not dry_run:
                    kg.add_entity(word)
                stats["entities_found"] += 1

    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="KG Backfill — build initial entity_relations from history")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done, don't modify DB")
    parser.add_argument("--facts-only", action="store_true", help="Only scan facts")
    parser.add_argument("--fabric-only", action="store_true", help="Only scan fabric entries")
    args = parser.parse_args()

    kg = KnowledgeGraph() if not args.dry_run else None

    print("🧠 KG Backfill: Building initial entity_relations from historical data")
    if args.dry_run:
        print("   ⚠️  DRY-RUN — no changes will be made\n")

    total_entities = 0
    total_relations = 0

    # Phase 1: Scan facts for entities and relations
    if not args.fabric_only:
        print("\n📄 Phase 1: Scraping facts...")
        s1 = backfill_from_facts(kg, args.dry_run)
        print(f"   Facts scanned: {s1['facts_scanned']}")
        print(f"   Entity candidates: {s1['entities_found']}")
        print(f"   Relation patterns matched: {s1['patterns_matched']}")
        print(f"   Relations extracted: {s1['relations_found']}")
        total_entities += s1['entities_found']
        total_relations += s1['relations_found']

        # Phase 2: fact_entities → mentioned_in relations
        print("\n🔗 Phase 2: Converting existing fact-entity associations...")
        s2 = backfill_from_existing_associations(kg, args.dry_run)
        print(f"   Associations scanned: {s2['associations_scanned']}")
        print(f"   Relations added: {s2['relations_added']}")
        total_relations += s2['relations_added']

    # Phase 3: Scan fabric entries for entities
    if not args.facts_only:
        print("\n📋 Phase 3: Scanning fabric entries...")
        s3 = backfill_from_fabric(kg, args.dry_run)
        print(f"   Entries scanned: {s3['entries_scanned']}")
        print(f"   Entity candidates: {s3['entities_found']}")
        total_entities += s3['entities_found']

    # Summary
    print(f"\n{'='*50}")
    if args.dry_run:
        print(f"DRY-RUN: Would add ~{total_entities} entity candidates and ~{total_relations} relations")
    else:
        print(f"✅ Backfill complete: added ~{total_entities} entity candidates, ~{total_relations} relations")
        # Verify
        conn = sqlite3.connect(str(MEMORY_DB))
        e = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        r = conn.execute("SELECT count(*) FROM entity_relations").fetchone()[0]
        conn.close()
        print(f"   Entities now: {e}")
        print(f"   Relations now: {r}")


if __name__ == "__main__":
    main()
