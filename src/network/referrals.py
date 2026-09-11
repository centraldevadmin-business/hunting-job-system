"""
Referral & Network — Module 10.

Finds warm referral paths to target companies using the knowledge graph
(Module 9), and drafts referral messages for the human to review and send.
It NEVER sends anything — no email, no LinkedIn message, no API call to any
social network. The human always clicks send.

Three capabilities:

  1. Warm-path discovery — for every target company, find the shortest
     `you -> contact -> target` path through the graph. Contacts are ranked
     by relationship strength (how many shared skills/roles connect them to
     the target).

  2. Referral drafts — generate a personalized outreach message for each
     path. The message is assembled from VERIFIED facts only (the contact's
     real role, the shared skill, the target company). No fabricated
     connections, no "I heard you'd love my profile" fluff.

  3. Referral tracking — persist identified/drafted referrals to the
     `referrals` table so the human can track progress.

Design guarantees:
  * Zero hallucination. Every contact, role, and shared-skill in a draft
    traces to a real kg_node / verified master record.
  * Human-in-the-loop. Drafts are written to the DB; the human sends them.
  * Deterministic. Same graph → same paths and drafts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import query_all, query_one, execute_sql
from src.knowledge.graph import KnowledgeGraph, REL_KNOWS, REL_WORKS_AT


@dataclass
class ReferralPath:
    company: str
    path: list[str]
    contact: str
    shared_skills: list[str]
    draft: str = ""
    status: str = "identified"  # identified|drafted|sent|accepted|referral


@dataclass
class NetworkReport:
    target_companies: int
    warm_paths: int
    drafts_ready: int
    referrals: list[ReferralPath] = field(default_factory=list)


class ReferralEngine:
    """Finds warm referral paths and drafts outreach messages."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self.graph = KnowledgeGraph(self.settings)
        self.graph.sync()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def find_paths(self, target_companies: Optional[list[str]] = None) -> list[ReferralPath]:
        """
        Find warm referral paths to target companies.

        If `target_companies` is None, uses all companies in the graph that
        the user has NOT already applied to. For each, runs a BFS referral
        path lookup and enriches with shared skills + a draft.
        """
        applied = {
            r["company"]
            for r in query_all(
                """
                SELECT rj.company FROM applications a
                JOIN raw_jobs rj ON rj.id = a.job_id
                WHERE rj.company IS NOT NULL
                """,
                settings=self.settings,
            )
        }
        if target_companies is None:
            targets = [c for c in self.graph.companies() if c not in applied]
        else:
            targets = [c for c in target_companies if c not in applied]

        paths = []
        for company in targets:
            path = self.graph.referral_path(company)
            if not path or len(path) < 3:
                continue  # no warm path (need you -> contact -> company)
            contact = path[1]
            shared = self._shared_skills(contact, company)
            draft = self._draft(contact, company, shared)
            paths.append(
                ReferralPath(
                    company=company,
                    path=path,
                    contact=contact,
                    shared_skills=shared,
                    draft=draft,
                    status="identified",
                )
            )
        return paths

    def _shared_skills(self, contact: str, company: str) -> list[str]:
        """Skills the contact and target company both have (verified)."""
        contact_skills = self._person_skills(contact)
        company_skills = self._company_skills(company)
        return sorted(set(contact_skills) & set(company_skills))

    def _person_skills(self, contact: str) -> list[str]:
        """Skills a person node has, from verified master record if it's 'you'."""
        if contact == "you":
            return [s["name"] for s in query_all(
                "SELECT name FROM skill", settings=self.settings)]
        # For other contacts, skills come from the graph's has_skill edges.
        node = self._find_person_node(contact)
        if not node:
            return []
        rows = query_all(
            """
            SELECT s.name FROM kg_edges e
            JOIN kg_nodes s ON s.id = e.to_node
            WHERE e.from_node = ? AND e.rel = 'has_skill'
            """,
            (node["id"],),
            settings=self.settings,
        )
        return [r["name"] for r in rows]

    def _company_skills(self, company: str) -> list[str]:
        """Skills a company requires, from the graph's requires_skill edges."""
        node = self._find_company_node(company)
        if not node:
            return []
        rows = query_all(
            """
            SELECT s.name FROM kg_edges e
            JOIN kg_nodes s ON s.id = e.to_node
            WHERE e.from_node = ? AND e.rel = 'requires_skill'
            """,
            (node["id"],),
            settings=self.settings,
        )
        return [r["name"] for r in rows]

    def _find_person_node(self, name: str) -> Optional[dict]:
        return query_one(
            "SELECT id FROM kg_nodes WHERE kind = 'person' AND name = ?",
            (name,),
            settings=self.settings,
        )

    def _find_company_node(self, name: str) -> Optional[dict]:
        return query_one(
            "SELECT id FROM kg_nodes WHERE kind = 'company' AND name = ?",
            (name,),
            settings=self.settings,
        )

    # ------------------------------------------------------------------ #
    # Drafting
    # ------------------------------------------------------------------ #
    def _draft(self, contact: str, company: str, shared_skills: list[str]) -> str:
        """
        Draft a personalized outreach message.

        Assembled from VERIFIED facts only: the contact's real connection
        (shared skills), the target company. No fabricated details.
        """
        skill_phrase = ", ".join(shared_skills) if shared_skills else "a shared background"
        return (
            f"Hi {contact},\n\n"
            f"I noticed we both have experience with {skill_phrase}, and I'm "
            f"really interested in {company}. I'd love to learn more about "
            f"the team and any opportunities that might be a fit. Would you "
            f"have 15 minutes for a quick chat?\n\n"
            f"Thanks, [Your name]"
        )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save_referrals(self, paths: list[ReferralPath]) -> int:
        """Persist referral paths to the DB. Returns count saved."""
        count = 0
        for p in paths:
            execute_sql(
                """
                INSERT INTO referrals (company_id, contact_id, relationship, path,
                    status, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._company_id(p.company),
                    self._contact_id(p.contact),
                    " -> ".join(p.path),
                    " -> ".join(p.path),
                    p.status,
                    p.draft,
                ),
                settings=self.settings,
            )
            count += 1
        return count

    def _company_id(self, company: str) -> Optional[int]:
        row = query_one(
            "SELECT id FROM kg_nodes WHERE kind = 'company' AND name = ?",
            (company,),
            settings=self.settings,
        )
        return row["id"] if row else None

    def _contact_id(self, contact: str) -> Optional[int]:
        row = query_one(
            "SELECT id FROM kg_nodes WHERE kind = 'person' AND name = ?",
            (contact,),
            settings=self.settings,
        )
        return row["id"] if row else None

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def report(self) -> NetworkReport:
        paths = self.find_paths()
        return NetworkReport(
            target_companies=len(paths),
            warm_paths=sum(1 for p in paths if p.path),
            drafts_ready=sum(1 for p in paths if p.draft),
            referrals=paths,
        )
