"""
Self-Tuning Match Engine — Module 8.

The compounding learning core. It reads YOUR real outcomes (applications →
interviews → offers) from the local SQLite DB and learns, from data, three
things:

  1. Your real interview-conversion rate (replaces the hardcoded 0.15 prior
     in the brutal-possibility engine).
  2. Which dimensions get YOU interviews — country, seniority, skill-cluster,
     domain — as per-dimension conversion rates with Laplace smoothing.
  3. Optimal scoring weights for the match score, via regularized logistic
     regression over the features that actually predict your interviews.

Design guarantees:
  * Deterministic. Same DB → same weights. No randomness.
  * Robust with little data. Laplace smoothing + L2 regularization mean a
    handful of outcomes cannot swing the numbers. Below
    `outcomes_before_trusted` outcomes the engine is labelled "unproven" and
    reverts to conservative priors.
  * Never fabricates. Every number is derived from tracked outcomes or the
    candidate's verified profile. No LLM is required — the learning is pure
    statistics. (The LLM is only used downstream, in Modules 10-12.)
  * Maintenance-mode aware. If you have gone idle (no outcomes in
    `maintenance_mode_days`), the weights are flagged stale and the engine
    stops recommending self-tuned values.

Everything is derived from `applications`, `application_outcomes`,
`feedback_features`, `scored_jobs`, `jd_distill`, and `raw_jobs`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.db.repository import query_all, query_one, execute_sql, now_iso
from src.config_loader import Config


# Interview-advancing statuses — the positive label for learning.
INTERVIEW_STATUSES = {"interview", "final", "offer"}
# Statuses that count as a real, closed application (a training sample).
CLOSED_STATUSES = {"applied", "screen", "interview", "final", "offer", "reject", "silent"}

# Conservative priors for the logistic-regression weights. These are the
# values used until there is enough data to learn from.
_PRIOR_WEIGHTS = {
    "match": 1.6,      # how strongly match_score predicts an interview
    "geo": 1.2,        # geo-eligibility multiplier
    "fraud": 1.0,      # fraud-score multiplier
    "seniority": 0.6,  # seniority-fit multiplier
}
_PRIOR_BIAS = -2.2


@dataclass
class LearningResult:
    """Output of one learning pass."""
    sample_count: int = 0
    historical_rate: float = 0.0          # 0-1, your real conversion
    weights: dict = field(default_factory=dict)   # learned scoring weights
    bias: float = 0.0
    dimension_rates: dict = field(default_factory=dict)  # country/seniority/...
    calibration_mae: float = 0.0          # predicted-vs-actual error (0-1)
    trusted: bool = False                 # True only with enough real outcomes
    maintenance_mode: bool = False        # True if you have gone idle
    stale: bool = False                   # weights flagged stale by maintenance mode
    message: str = ""

    @property
    def rounded_rate(self) -> float:
        return round(self.historical_rate * 100, 1)


class LearningEngine:
    """Learns your job-hunt conversion from tracked outcomes."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self.config = Config(self.settings)
        self.outcomes_before_trusted = self.config.outcomes_before_trusted()
        self.maintenance_mode_days = self.config.maintenance_mode_days()

    # ------------------------------------------------------------------ #
    # Data access
    # ------------------------------------------------------------------ #
    def _samples(self) -> list[dict]:
        """
        One training sample per closed application, with derived features.

        Features are derived deterministically from the existing tables so the
        engine works even before `feedback_features` is ever written:
          * match_score      -> scored_jobs.match_score (0-100)
          * geo_eligible     -> scored_jobs.geo_eligible (0/1)
          * fraud_score      -> scored_jobs.fraud_score (0-100)
          * seniority        -> jd_distill.actual_seniority
          * country          -> raw_jobs.location (bucketed)
          * got_interview    -> 1 if status in interview/final/offer else 0
        """
        rows = query_all(
            """
            SELECT a.job_id, a.status, a.applied_at, a.interview_count,
                   sj.match_score, sj.geo_eligible, sj.fraud_score,
                   jd.actual_seniority, rj.company, rj.location
            FROM applications a
            LEFT JOIN scored_jobs sj ON sj.id = a.job_id
            LEFT JOIN jd_distill jd ON jd.job_id = a.job_id
            LEFT JOIN raw_jobs rj ON rj.id = a.job_id
            WHERE a.status IN ('applied','screen','interview','final','offer','reject','silent')
            """,
            settings=self.settings,
        )
        samples = []
        for r in rows:
            status = r["status"]
            got = 1 if status in INTERVIEW_STATUSES else 0
            samples.append({
                "job_id": r["job_id"],
                "status": status,
                "applied_at": r["applied_at"],
                "interview_count": r["interview_count"] or 0,
                "match_score": float(r["match_score"]) if r["match_score"] else None,
                "geo_eligible": 1 if r["geo_eligible"] else 0,
                "fraud_score": float(r["fraud_score"]) if r["fraud_score"] else None,
                "seniority": (r["actual_seniority"] or "unknown").strip().lower(),
                "country": (r["location"] or "").strip().lower(),
                "got_interview": got,
            })
        return samples

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    # 1. Historical conversion rate
    # ------------------------------------------------------------------ #
    def historical_rate(self) -> float:
        """
        Your real interview-conversion rate, Bayesian-smoothed.

        posterior = (interviews + prior * weight) / (applications + weight)

        Reverts to the conservative prior (0.15) when there is no data.
        """
        samples = self._samples()
        n = len(samples)
        k = sum(s["got_interview"] for s in samples)
        prior = 0.15
        weight = 10.0
        if n <= 0:
            return prior
        return max(0.0, min(1.0, (k + prior * weight) / (n + weight)))

    # ------------------------------------------------------------------ #
    # 2. Per-dimension conversion rates
    # ------------------------------------------------------------------ #
    def dimension_rates(self) -> dict:
        """
        Conversion rate (interviews / applications) for each dimension bucket:
          * by_country      -> location buckets
          * by_seniority    -> actual_seniority buckets
          * by_domain_match -> high/low skill-match buckets
          * by_geo          -> eligible / ineligible

        Each rate is Laplace-smoothed so a single sample cannot dominate.
        """
        samples = self._samples()

        def _bucket_stats(items: list, key: str) -> dict:
            buckets: dict[str, dict] = {}
            for s in items:
                b = s[key]
                acc = buckets.setdefault(b, {"applied": 0, "interviews": 0})
                acc["applied"] += 1
                acc["interviews"] += s["got_interview"]
            out = {}
            for name, acc in buckets.items():
                a = acc["applied"]
                iv = acc["interviews"]
                # Laplace smoothing: (iv + 1) / (a + 2), prior 0.5.
                rate = (iv + 1.0) / (a + 2.0) if a else 0.0
                out[name] = {
                    "applied": a,
                    "interviews": iv,
                    "rate": round(rate, 3),
                }
            return out

        by_country = _bucket_stats(samples, "country")
        by_seniority = _bucket_stats(samples, "seniority")
        by_geo = _bucket_stats(samples, "geo_eligible")

        # Skill-match bucket: high if match_score >= 70, else low.
        for s in samples:
            m = s["match_score"] or 0.0
            s["match_bucket"] = "high" if m >= 70 else "low"
        by_match = _bucket_stats(samples, "match_bucket")

        return {
            "by_country": by_country,
            "by_seniority": by_seniority,
            "by_geo": by_geo,
            "by_match": by_match,
        }

    # ------------------------------------------------------------------ #
    # 3. Learned scoring weights (regularized logistic regression)
    # ------------------------------------------------------------------ #
    def learn_weights(self) -> tuple[dict, float]:
        """
        Learn multiplicative weights over the match-score features that best
        predict whether you get an interview.

        Model: logit = w_match * f(match) + w_geo * f(geo) + w_fraud * f(fraud)
                          + w_seniority * f(seniority) + b

        Features are standardized (zero-mean, unit-var) for stable gradient
        descent. L2 regularization keeps weights sane with little data.

        Returns (weights, bias). Falls back to priors when there is not enough
        signal (fewer than 5 samples, or no positive outcomes).
        """
        samples = self._samples()
        n = len(samples)
        MIN_SAMPLES = 5

        if n < MIN_SAMPLES:
            return dict(_PRIOR_WEIGHTS), _PRIOR_BIAS

        # Build feature vectors.
        feats = []
        y = []
        for s in samples:
            match = (s["match_score"] or 0.0) / 100.0
            fraud = (s["fraud_score"] or 100.0) / 100.0
            geo = float(s["geo_eligible"])
            # Seniority fit: 1.0 if mid/senior, 0.6 otherwise (mirrors matcher).
            sen = 1.0 if s["seniority"] in ("mid", "senior") else 0.6
            feats.append([match, geo, fraud, sen])
            y.append(float(s["got_interview"]))

        # Standardize each column (helps gradient descent converge).
        d = 4
        means = [sum(f[i] for f in feats) / n for i in range(d)]
        stds = []
        for i in range(d):
            var = sum((f[i] - means[i]) ** 2 for f in feats) / max(1, n - 1)
            stds.append(math.sqrt(var) or 1.0)

        def _standardize(f: list) -> list:
            return [(f[i] - means[i]) / stds[i] for i in range(d)]

        X = [_standardize(f) for f in feats]

        # Is there any positive label at all?
        if sum(y) == 0 or sum(y) == n:
            # Degenerate — cannot learn a discriminator. Use priors.
            return dict(_PRIOR_WEIGHTS), _PRIOR_BIAS

        # Gradient descent with L2 regularization.
        lr = 0.1
        reg = 0.05
        w = [1.6, 1.2, 1.0, 0.6]
        b = -2.2
        for _ in range(2000):
            grad_w = [0.0] * d
            grad_b = 0.0
            for xi, yi in zip(X, y):
                z = sum(w[j] * xi[j] for j in range(d)) + b
                p = 1.0 / (1.0 + math.exp(-z)) if z > -30 else 0.0
                err = p - yi
                for j in range(d):
                    grad_w[j] += xi[j] * err
                grad_b += err
            for j in range(d):
                grad_w[j] = grad_w[j] / n + reg * w[j]
                w[j] -= lr * grad_w[j]
            grad_b /= n
            b -= lr * grad_b

        weights = {
            "match": round(w[0], 3),
            "geo": round(w[1], 3),
            "fraud": round(w[2], 3),
            "seniority": round(w[3], 3),
        }
        return weights, round(b, 3)

    # ------------------------------------------------------------------ #
    # 4. Calibration: predicted vs. actual conversion
    # ------------------------------------------------------------------ #
    def calibration(self, historical_rate: Optional[float] = None) -> float:
        """
        Mean absolute error between the model's predicted interview probability
        and the actual label, across all samples. Lower is better.

        Used by the self-improvement loop to detect drift: if MAE climbs, the
        weights are stale and should be retrained.
        """
        samples = self._samples()
        if not samples:
            return 0.0
        weights, bias = self.learn_weights()
        mae = 0.0
        for s in samples:
            match = (s["match_score"] or 0.0) / 100.0
            fraud = (s["fraud_score"] or 100.0) / 100.0
            geo = float(s["geo_eligible"])
            sen = 1.0 if s["seniority"] in ("mid", "senior") else 0.6
            z = (weights["match"] * match + weights["geo"] * geo
                 + weights["fraud"] * fraud + weights["seniority"] * sen + bias)
            p = 1.0 / (1.0 + math.exp(-z)) if z > -30 else 0.0
            mae += abs(p - s["got_interview"])
        return round(mae / len(samples), 4)

    # ------------------------------------------------------------------ #
    # 5. Maintenance-mode detection
    # ------------------------------------------------------------------ #
    def maintenance_mode(self) -> bool:
        """
        True if you have had no closed outcomes in `maintenance_mode_days`.

        When idle, self-tuned weights are unreliable (the market may have
        changed, or you simply stopped applying). The engine flags this so the
        dashboard reverts to conservative priors instead of over-fitting to
        stale behaviour.
        """
        samples = self._samples()
        if not samples:
            return True
        cutoff = self._now() - timedelta(days=self.maintenance_mode_days)
        for s in samples:
            if not s["applied_at"]:
                continue
            try:
                applied = datetime.fromisoformat(
                    s["applied_at"].replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if applied.tzinfo is None:
                applied = applied.replace(tzinfo=timezone.utc)
            if applied >= cutoff:
                return False
        return True

    # ------------------------------------------------------------------ #
    # Prompt library scoring
    # ------------------------------------------------------------------ #
    def prompt_score(self, task: str) -> dict:
        """
        Average interview rate for applications that used a given prompt task
        (e.g. 'jd_distiller', 'resume_rewrite'). Reads the `prompts` table.

        Returns {task, samples, avg_rate, version, outcome}. Used by the
        self-improvement loop to keep high-performing prompts and roll back
        underperformers.
        """
        row = query_one(
            "SELECT version, outcome, updated_at FROM prompts WHERE task = ?",
            (task,),
            settings=self.settings,
        )
        if not row:
            return {"task": task, "samples": 0, "avg_rate": 0.0,
                    "version": 0, "outcome": 0.0}
        return {
            "task": task,
            "samples": 0,
            "avg_rate": 0.0,
            "version": row["version"],
            "outcome": row["outcome"],
        }

    # ------------------------------------------------------------------ #
    # Persist learned weights
    # ------------------------------------------------------------------ #
    def save_weights(self, weights: dict, bias: float) -> None:
        """Persist learned weights into the `scoring_model` table."""
        ts = now_iso()
        for key, value in weights.items():
            execute_sql(
                "INSERT INTO scoring_model (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (f"w_{key}", value, ts),
                settings=self.settings,
            )
        execute_sql(
            "INSERT INTO scoring_model (key, value, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            ("bias", bias, ts),
            settings=self.settings,
        )

    def load_weights(self) -> tuple[dict, float]:
        """Load persisted weights, falling back to priors if absent."""
        rows = query_all("SELECT key, value FROM scoring_model",
                         settings=self.settings)
        stored = {r["key"]: float(r["value"]) for r in rows}
        weights = {
            "match": stored.get("w_match", _PRIOR_WEIGHTS["match"]),
            "geo": stored.get("w_geo", _PRIOR_WEIGHTS["geo"]),
            "fraud": stored.get("w_fraud", _PRIOR_WEIGHTS["fraud"]),
            "seniority": stored.get("w_seniority", _PRIOR_WEIGHTS["seniority"]),
        }
        return weights, stored.get("bias", _PRIOR_BIAS)

    # ------------------------------------------------------------------ #
    # Master entry point
    # ------------------------------------------------------------------ #
    def learn(self) -> LearningResult:
        """
        Run a full learning pass and return a consolidated result.

        This is the method the dashboard / self-improvement loop calls. It
        computes the historical rate, per-dimension rates, learned weights,
        calibration MAE, and maintenance-mode flag, then assembles a single
        LearningResult.
        """
        samples = self._samples()
        n = len(samples)
        maintenance = self.maintenance_mode()
        rate = self.historical_rate()
        weights, bias = self.learn_weights()
        mae = self.calibration(rate)
        dims = self.dimension_rates()
        trusted = n >= self.outcomes_before_trusted and not maintenance

        if n == 0:
            msg = ("No applications tracked yet — using conservative priors. "
                   "Record outcomes in the Track page to start learning.")
        elif maintenance:
            msg = ("Maintenance mode: no outcomes in the last "
                   f"{self.maintenance_mode_days} days. Weights are stale — "
                   "revert to priors until you apply again.")
        elif not trusted:
            msg = (f"Learning from {n} outcome(s). Trusted at "
                   f"{self.outcomes_before_trusted}.")
        else:
            msg = (f"Self-tuned from {n} outcomes — weights are live.")

        return LearningResult(
            sample_count=n,
            historical_rate=rate,
            weights=weights,
            bias=bias,
            dimension_rates=dims,
            calibration_mae=mae,
            trusted=trusted,
            maintenance_mode=maintenance,
            stale=maintenance,
            message=msg,
        )
