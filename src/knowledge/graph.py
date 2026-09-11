"""
Knowledge Graph — Module 9.

A lightweight, dependency-free graph of the candidate's job-hunt world:
companies, people, roles, skills, and outcomes. It is persisted in the
`kg_nodes` / `kg_edges` tables and backed by an in-memory adjacency structure
so lookups are fast.

It powers three advanced capabilities:

  1. Similar-company discovery — companies adjacent to ones you got interviews
     at (weighted by edge strength), surfaced as new, unvetted leads.
  2. Referral-path finding — shortest path `you -> contact -> target-company`
     via graph traversal (used by Module 10).
  3. Company clustering — groups of companies that share roles/skills, so the
     hunt can recommend whole sectors at once.

Design guarantees:
  * Deterministic. Same DB → same graph.
  * Never fabricates. Nodes are only created from real data: the candidate's
    verified master record, collected jobs, and tracked outcomes.
  * Idempotent. Re-running the sync does not duplicate nodes or edges.
  * No network, no LLM. Pure graph theory over local SQLite.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import query_all, query_one, execute_sql, now_iso


# Node kinds.
COMPANY = "company"
PERSON = "person"
ROLE = "role"
SKILL = "skill"
OUTCOME = "outcome"

# Edge relationship types.
REL_WORKS_AT = "works_at"          # person -> company
REL_KNOWS = "knows"                # person -> person
REL_APPLIED_TO = "applied_to"      # person -> company (your applications)
REL_SIMILAR_TO = "similar_to"      # company -> company (shared signals)
REL_REFERS_TO = "refers_to"        # person -> company (referral target)
REL_HAS_SKILL = "has_skill"        # person -> skill
REL_REQUIRES_SKILL = "requires_skill"  # role/company -> skill
REL_HAS_ROLE = "has_role"          # company -> role


@dataclass
class Node:
    id: int
    kind: str
    name: str
    entity_id: Optional[str] = None
    payload: dict = field(default_factory=dict)


@dataclass
class Edge:
    from_id: int
    to_id: int
    rel: str
    weight: float = 1.0


@dataclass
class GraphStats:
    companies: int = 0
    people: int = 0
    roles: int = 0
    skills: int = 0
    edges: int = 0


class KnowledgeGraph:
    """A persisted, in-memory knowledge graph over the career DB."""

    _settings: Optional[dict] = None

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings
        KnowledgeGraph._settings = settings
        self._nodes: dict[int, Node] = {}
        self._edges: list[Edge] = []
        self._adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """Load nodes + edges from SQLite into memory (idempotent)."""
        if self._loaded:
            return
        rows = query_all(
            "SELECT id, kind, name, entity_id, payload FROM kg_nodes ORDER BY id",
            settings=self.settings,
        )
        for r in rows:
            self._nodes[r["id"]] = Node(
                id=r["id"],
                kind=r["kind"],
                name=r["name"],
                entity_id=r["entity_id"],
                payload=self._decode_payload(r["payload"]),
            )
        edges = query_all(
            "SELECT from_node, to_node, rel, weight FROM kg_edges ORDER BY id",
            settings=self.settings,
        )
        for e in edges:
            self._edges.append(
                Edge(e["from_node"], e["to_node"], e["rel"], float(e["weight"] or 1.0))
            )
            self._adj[e["from_node"]].append((e["to_node"], float(e["weight"] or 1.0)))
        self._loaded = True

    @staticmethod
    def _decode_payload(payload: Optional[str]) -> dict:
        import json
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _encode_payload(payload: dict) -> Optional[str]:
        import json
        if not payload:
            return None
        return json.dumps(payload)

    def _upsert_node(self, kind: str, name: str, entity_id: Optional[str] = None,
                     payload: Optional[dict] = None) -> int:
        """Insert or fetch a node by (kind, entity_id). Returns node id."""
        if entity_id:
            row = query_one(
                "SELECT id FROM kg_nodes WHERE kind = ? AND entity_id = ?",
                (kind, entity_id),
                settings=self.settings,
            )
            if row:
                return row["id"]
        # No entity_id → always create a fresh node.
        execute_sql(
            "INSERT INTO kg_nodes (kind, name, entity_id, payload, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, name, entity_id, self._encode_payload(payload or {}), now_iso()),
            settings=self.settings,
        )
        row = query_one(
            "SELECT id FROM kg_nodes WHERE kind = ? AND name = ? ORDER BY id DESC LIMIT 1",
            (kind, name),
            settings=self.settings,
        )
        return row["id"]

    def _upsert_edge(self, from_id: int, to_id: int, rel: str,
                     weight: float = 1.0) -> None:
        """Insert an edge unless an identical (from,to,rel) already exists."""
        existing = query_one(
            "SELECT weight FROM kg_edges WHERE from_node = ? AND to_node = ? AND rel = ?",
            (from_id, to_id, rel),
            settings=self.settings,
        )
        if existing:
            return
        execute_sql(
            "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (from_id, to_id, rel, weight, now_iso()),
            settings=self.settings,
        )

    # ------------------------------------------------------------------ #
    # Sync from the DB
    # ------------------------------------------------------------------ #
    def sync(self) -> GraphStats:
        """
        Rebuild the graph from the career DB. Idempotent — safe to call
        repeatedly. Returns aggregate stats.
        """
        self.load()
        stats = GraphStats()

        # Companies (from raw_jobs + master record).
        for r in query_all(
            "SELECT DISTINCT company FROM raw_jobs WHERE company IS NOT NULL AND company != ''",
            settings=self.settings,
        ):
            cid = self._upsert_node(COMPANY, r["company"], entity_id=r["company"])
            stats.companies += 1

        # Skills (from the verified master record).
        for r in query_all("SELECT name FROM skill ORDER BY name",
                           settings=self.settings):
            sid = self._upsert_node(SKILL, r["name"], entity_id=r["name"])
            stats.skills += 1

        # Roles (from job titles + target roles).
        for r in query_all(
            "SELECT DISTINCT role_title FROM raw_jobs "
            "WHERE role_title IS NOT NULL AND role_title != ''",
            settings=self.settings,
        ):
            rid = self._upsert_node(ROLE, r["role_title"], entity_id=r["role_title"])
            stats.roles += 1

        # Edges: company -> requires_skill (from JD tokens), person -> has_skill.
        skills = [r["name"] for r in query_all("SELECT name FROM skill",
                                               settings=self.settings)]
        skill_ids = {
            r["name"]: self._upsert_node(SKILL, r["name"], entity_id=r["name"])
            for r in query_all("SELECT name FROM skill", settings=self.settings)
        }
        for r in query_all(
            "SELECT id, company, jd FROM raw_jobs WHERE jd IS NOT NULL AND jd != ''",
            settings=self.settings,
        ):
            cid = query_one(
                "SELECT id FROM kg_nodes WHERE kind = ? AND entity_id = ?",
                (COMPANY, r["company"]),
                settings=self.settings,
            )
            if not cid:
                continue
            tokens = self._skill_tokens(r["jd"])
            for tok in tokens:
                if tok in skill_ids:
                    self._upsert_edge(cid["id"], skill_ids[tok], REL_REQUIRES_SKILL)
                    stats.edges += 1

        # Edges: person (you) -> works_at company (from master employment).
        you = self._upsert_node(PERSON, "you", entity_id="candidate")
        for r in query_all(
            "SELECT company FROM employment WHERE company IS NOT NULL AND company != ''",
            settings=self.settings,
        ):
            cid = query_one(
                "SELECT id FROM kg_nodes WHERE kind = ? AND entity_id = ?",
                (COMPANY, r["company"]),
                settings=self.settings,
            )
            if cid:
                self._upsert_edge(you, cid["id"], REL_WORKS_AT)
                stats.edges += 1

        # Edges: person -> applied_to company (from tracked applications).
        for r in query_all(
            "SELECT a.job_id, rj.company FROM applications a "
            "JOIN raw_jobs rj ON rj.id = a.job_id",
            settings=self.settings,
        ):
            cid = query_one(
                "SELECT id FROM kg_nodes WHERE kind = ? AND entity_id = ?",
                (COMPANY, r["company"]),
                settings=self.settings,
            )
            if cid:
                self._upsert_edge(you, cid["id"], REL_APPLIED_TO)
                stats.edges += 1

        # Reload everything from the DB so the in-memory view reflects the
        # nodes/edges we just inserted, then recompute adjacency.
        self._reload_from_db()
        self._reload_adjacency()
        stats.edges = len(self._edges)
        return stats

    def _reload_from_db(self) -> None:
        """Re-read all nodes + edges from SQLite into memory."""
        rows = query_all(
            "SELECT id, kind, name, entity_id, payload FROM kg_nodes ORDER BY id",
            settings=self.settings,
        )
        self._nodes = {
            r["id"]: Node(
                id=r["id"],
                kind=r["kind"],
                name=r["name"],
                entity_id=r["entity_id"],
                payload=self._decode_payload(r["payload"]),
            )
            for r in rows
        }
        edges = query_all(
            "SELECT from_node, to_node, rel, weight FROM kg_edges ORDER BY id",
            settings=self.settings,
        )
        self._edges = [
            Edge(e["from_node"], e["to_node"], e["rel"], float(e["weight"] or 1.0))
            for e in edges
        ]

    def _reload_adjacency(self) -> None:
        self._adj = defaultdict(list)
        for e in self._edges:
            self._adj[e.from_id].append((e.to_id, e.weight))

    @staticmethod
    def _skill_tokens(jd: str) -> set[str]:
        import re
        known = {
            "python", "sql", "power bi", "tableau", "excel", "pandas",
            "numpy", "scikit-learn", "machine learning", "ml", "analytics",
            "data analyst", "business analyst", "communication", "leadership",
        }
        text = (jd or "").lower()
        found = set()
        for tok in known:
            if tok in text:
                found.add(tok)
        return found

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def stats(self) -> GraphStats:
        self.load()
        counts = {"company": 0, "person": 0, "role": 0, "skill": 0}
        for n in self._nodes.values():
            if n.kind in counts:
                counts[n.kind] += 1
        return GraphStats(
            companies=counts["company"],
            people=counts["person"],
            roles=counts["role"],
            skills=counts["skill"],
            edges=len(self._edges),
        )

    def companies(self) -> list[str]:
        self.load()
        return sorted({n.name for n in self._nodes.values() if n.kind == COMPANY})

    def skills(self) -> list[str]:
        self.load()
        return sorted({n.name for n in self._nodes.values() if n.kind == SKILL})

    def _find_node(self, kind: str, entity_id: str) -> Optional[int]:
        row = query_one(
            "SELECT id FROM kg_nodes WHERE kind = ? AND entity_id = ?",
            (kind, entity_id),
            settings=self.settings,
        )
        return row["id"] if row else None

    def _neighbors(self, node_id: int) -> list[tuple[int, float]]:
        return list(self._adj.get(node_id, []))

    def _incoming(self, node_id: int) -> list[tuple[int, float]]:
        """Nodes with an edge pointing TO node_id (reverse adjacency)."""
        return [
            (e.from_id, e.weight)
            for e in self._edges
            if e.to_id == node_id
        ]

    def similar_companies(self, seed_company: str, limit: int = 10) -> list[dict]:
        """
        Discover companies similar to `seed_company` by shared skills.

        Walks two hops: seed_company -> skill -> other companies that require
        that skill. Companies reached through more shared skills rank higher.
        Excludes the seed itself.
        """
        self.load()
        seed_id = self._find_node(COMPANY, seed_company)
        if not seed_id:
            return []

        scores: dict[int, float] = defaultdict(float)
        for skill_id, weight in self._neighbors(seed_id):
            for comp_id, w in self._incoming(skill_id):
                if comp_id == seed_id:
                    continue
                scores[comp_id] += w

        out = []
        for comp_id, score in sorted(scores.items(), key=lambda x: -x[1]):
            node = self._nodes.get(comp_id)
            if not node:
                continue
            out.append({"company": node.name, "shared_skills": round(score, 2)})
            if len(out) >= limit:
                break
        return out

    def referral_path(self, target_company: str,
                      max_hops: int = 4) -> Optional[list[str]]:
        """
        Find the shortest warm path from `you` to `target_company`.

        BFS over works_at / knows / applied_to edges. Returns the path as a
        list of names (["you", "Alice", "Acme"]) or None if unreachable.
        """
        self.load()
        you_id = self._find_node(PERSON, "candidate")
        target_id = self._find_node(COMPANY, target_company)
        if not you_id or not target_id:
            return None

        prev: dict[int, Optional[int]] = {you_id: None}
        queue = deque([you_id])
        while queue:
            cur = queue.popleft()
            if cur == target_id:
                return self._reconstruct(prev, cur)
            for nxt, _ in self._neighbors(cur):
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        return None

    def _reconstruct(self, prev: dict[int, Optional[int]], end: int) -> list[str]:
        chain = []
        node = end
        while node is not None:
            chain.append(node)
            node = prev[node]
        chain.reverse()
        names = []
        for nid in chain:
            node = self._nodes.get(nid, Node(nid, "unknown", ""))
            names.append("you" if nid == self._you_id() else node.name)
        return names

    def _you_id(self) -> Optional[int]:
        row = query_one(
            "SELECT id FROM kg_nodes WHERE kind = 'person' AND entity_id = 'candidate'",
            settings=self._settings,
        )
        return row["id"] if row else None

    def company_clusters(self) -> list[dict]:
        """
        Group companies by shared primary skill. Returns a list of clusters,
        each {skill, companies: [...]}. Useful for recommending whole sectors.
        """
        self.load()
        by_skill: dict[str, list[str]] = defaultdict(list)
        for e in self._edges:
            if e.rel != REL_REQUIRES_SKILL:
                continue
            skill_node = self._nodes.get(e.to_id)
            comp_node = self._nodes.get(e.from_id)
            if skill_node and comp_node:
                by_skill[skill_node.name].append(comp_node.name)
        clusters = []
        for skill, companies in sorted(by_skill.items()):
            clusters.append({"skill": skill, "companies": sorted(set(companies))})
        return sorted(clusters, key=lambda c: -len(c["companies"]))
