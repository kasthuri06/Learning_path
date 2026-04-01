"""
personalization_engine.py

Core data structures and serialization helpers for the AI Personalized Learning Path feature.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TopicSignal:
    """Per-topic behavioral signal captured from user interactions."""
    difficulty: int    # 1–5
    engagement: float  # 0.0–1.0


@dataclass
class Learning_Profile:
    """Computed learning profile for a single user."""
    pace: float                              # topics/week, >= 0
    preferred_style: str                     # "visual" | "practical" | "theoretical" | "balanced"
    topic_signals: dict[str, TopicSignal]    # topic name -> TopicSignal
    computed_at: str                         # ISO 8601 timestamp


# ---------------------------------------------------------------------------
# Default profile
# ---------------------------------------------------------------------------

def _default_profile() -> Learning_Profile:
    """Return a fresh default Learning_Profile with the current timestamp."""
    return Learning_Profile(
        pace=1.0,
        preferred_style="balanced",
        topic_signals={},
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


DEFAULT_PROFILE: Learning_Profile = _default_profile()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def serialize_profile(profile: Learning_Profile) -> str:
    """Serialize a Learning_Profile to a JSON string.

    Schema:
        {
            "pace": float,
            "preferred_style": str,
            "topic_signals": {topic: {"difficulty": int, "engagement": float}},
            "computed_at": str  (ISO 8601)
        }
    """
    data = {
        "pace": profile.pace,
        "preferred_style": profile.preferred_style,
        "topic_signals": {
            topic: {"difficulty": sig.difficulty, "engagement": sig.engagement}
            for topic, sig in profile.topic_signals.items()
        },
        "computed_at": profile.computed_at,
    }
    return json.dumps(data)


def deserialize_profile(json_str: str) -> Learning_Profile:
    """Deserialize a JSON string into a Learning_Profile.

    Validates:
    - All required top-level fields are present.
    - pace >= 0.
    - Each topic signal has difficulty in [1, 5] and engagement in [0.0, 1.0].

    Returns the default profile (pace=1.0, preferred_style="balanced",
    topic_signals={}, computed_at=now) on ANY error, and logs a warning.
    """
    try:
        data = json.loads(json_str)

        # --- required field presence ---
        required = {"pace", "preferred_style", "topic_signals", "computed_at"}
        missing = required - data.keys()
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        pace = data["pace"]
        preferred_style = data["preferred_style"]
        topic_signals_raw = data["topic_signals"]
        computed_at = data["computed_at"]

        # --- pace validation ---
        if not isinstance(pace, (int, float)) or pace < 0:
            raise ValueError(f"Invalid pace value: {pace!r}")

        # --- topic_signals validation ---
        if not isinstance(topic_signals_raw, dict):
            raise ValueError("topic_signals must be a dict")

        topic_signals: dict[str, TopicSignal] = {}
        for topic, sig_data in topic_signals_raw.items():
            if not isinstance(sig_data, dict):
                raise ValueError(f"Signal for topic {topic!r} must be a dict")

            difficulty = sig_data.get("difficulty")
            engagement = sig_data.get("engagement")

            if difficulty is None or engagement is None:
                raise ValueError(
                    f"Signal for topic {topic!r} missing difficulty or engagement"
                )
            if not isinstance(difficulty, int) or not (1 <= difficulty <= 5):
                raise ValueError(
                    f"difficulty for topic {topic!r} must be int in [1, 5], got {difficulty!r}"
                )
            if not isinstance(engagement, (int, float)) or not (0.0 <= engagement <= 1.0):
                raise ValueError(
                    f"engagement for topic {topic!r} must be float in [0.0, 1.0], got {engagement!r}"
                )

            topic_signals[topic] = TopicSignal(
                difficulty=difficulty,
                engagement=float(engagement),
            )

        return Learning_Profile(
            pace=float(pace),
            preferred_style=str(preferred_style),
            topic_signals=topic_signals,
            computed_at=str(computed_at),
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning("deserialize_profile failed, returning default profile: %s", exc)
        return _default_profile()


# ---------------------------------------------------------------------------
# Behavior Tracker
# ---------------------------------------------------------------------------

class Behavior_Tracker:
    """Records user interaction events and marks the learning profile stale."""

    def record_event(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        roadmap_id: int,
        topic: str,
        event_type: str,
        payload: dict,
    ) -> None:
        """Insert a behavior event row and mark the user's profile stale.

        On sqlite3.Error the error is logged and the method returns without
        raising, so the caller's operation is never blocked (Requirement 1.7).
        """
        try:
            payload_json = json.dumps(payload)
            created_at = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                INSERT INTO behavior_events
                    (user_id, roadmap_id, topic, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, roadmap_id, topic, event_type, payload_json, created_at),
            )

            # Requirement 2.7 — mark profile stale so it is recomputed on next access
            conn.execute(
                """
                UPDATE user_learning_profiles
                SET stale = 1
                WHERE user_id = ?
                """,
                (user_id,),
            )

            conn.commit()
        except sqlite3.Error as exc:
            logger.error("Behavior_Tracker.record_event failed: %s", exc)


# ---------------------------------------------------------------------------
# Personalization Engine
# ---------------------------------------------------------------------------

class Personalization_Engine:
    """Computes and caches per-user Learning_Profiles and pace suggestions."""

    # ------------------------------------------------------------------
    # mark_stale
    # ------------------------------------------------------------------

    def mark_stale(self, conn: sqlite3.Connection, user_id: int) -> None:
        """Set stale=1 in user_learning_profiles for the given user."""
        try:
            conn.execute(
                "UPDATE user_learning_profiles SET stale = 1 WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.error("Personalization_Engine.mark_stale failed: %s", exc)

    # ------------------------------------------------------------------
    # compute_profile
    # ------------------------------------------------------------------

    def compute_profile(self, conn: sqlite3.Connection, user_id: int) -> Learning_Profile:
        """Derive a Learning_Profile from behavioral data and persist it.

        Queries: behavior_events, progress, topic_sessions, resource_progress,
                 quiz_results.
        """
        now = datetime.now(timezone.utc)

        # ---- pace: distinct topics completed in the last 4 weeks ----
        four_weeks_ago = datetime.fromtimestamp(
            now.timestamp() - 4 * 7 * 24 * 3600, tz=timezone.utc
        ).isoformat()

        row = conn.execute(
            """
            SELECT COUNT(DISTINCT topic)
            FROM progress
            WHERE user_id = ?
              AND status = 'completed'
              AND roadmap_id IS NOT NULL
              AND completed_at >= ?
            """,
            (user_id, four_weeks_ago),
        ).fetchone()
        completed_topics_4w = row[0] if row else 0
        pace = max(0.0, completed_topics_4w / 4.0)

        # ---- preferred_style: video vs article resource completions ----
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN resource_url LIKE '%youtube%'
                              OR resource_url LIKE '%video%' THEN 1 ELSE 0 END),
                COUNT(*)
            FROM resource_progress
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        video_count = row[0] or 0
        total = row[1] or 0
        article_count = total - video_count

        if video_count / (total + 0.001) > 0.6:
            preferred_style = "visual"
        elif article_count / (total + 0.001) > 0.6:
            preferred_style = "theoretical"
        elif total > 5:
            preferred_style = "practical"
        else:
            preferred_style = "balanced"

        # ---- per-topic difficulty & engagement ----
        # pace_estimate_per_topic: if pace > 0 use 1/pace (hours per topic proxy),
        # else fall back to 1.0 to avoid division by zero.
        pace_estimate_per_topic = (1.0 / pace) if pace > 0 else 1.0

        # Fetch all topics the user has any signal for
        topics_rows = conn.execute(
            """
            SELECT DISTINCT topic FROM (
                SELECT topic FROM topic_sessions WHERE user_id = ?
                UNION
                SELECT topic FROM quiz_results   WHERE user_id = ?
                UNION
                SELECT topic FROM resource_progress WHERE user_id = ?
                UNION
                SELECT topic FROM behavior_events WHERE user_id = ?
            )
            """,
            (user_id, user_id, user_id, user_id),
        ).fetchall()
        topics = [r[0] for r in topics_rows]

        topic_signals: dict[str, TopicSignal] = {}

        for topic in topics:
            # --- difficulty ---
            quiz_row = conn.execute(
                """
                SELECT score FROM quiz_results
                WHERE user_id = ? AND topic = ?
                ORDER BY taken_at DESC LIMIT 1
                """,
                (user_id, topic),
            ).fetchone()
            quiz_score = quiz_row[0] if quiz_row else None

            time_row = conn.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0)
                FROM topic_sessions
                WHERE user_id = ? AND topic = ?
                """,
                (user_id, topic),
            ).fetchone()
            time_on_topic = time_row[0] if time_row else 0.0

            difficulty = 3
            if quiz_score is not None:
                if quiz_score >= 80:
                    difficulty -= 1
                elif quiz_score < 50:
                    difficulty += 1
            if time_on_topic > 2 * pace_estimate_per_topic:
                difficulty += 1
            difficulty = max(1, min(5, difficulty))

            # --- engagement ---
            res_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_res,
                    SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS completed_res
                FROM resource_progress
                WHERE user_id = ? AND topic = ?
                """,
                (user_id, topic),
            ).fetchone()
            total_res = res_row[0] or 0
            completed_res = res_row[1] or 0
            resource_completion_rate = (completed_res / total_res) if total_res > 0 else 0.0

            normalized_time = min(1.0, time_on_topic / 60.0)

            note_row = conn.execute(
                """
                SELECT COUNT(*) FROM behavior_events
                WHERE user_id = ? AND topic = ? AND event_type = 'note_added'
                """,
                (user_id, topic),
            ).fetchone()
            has_notes = 1 if (note_row and note_row[0] > 0) else 0

            engagement = (
                0.5 * resource_completion_rate
                + 0.3 * normalized_time
                + 0.2 * has_notes
            )
            engagement = max(0.0, min(1.0, engagement))

            topic_signals[topic] = TopicSignal(difficulty=difficulty, engagement=engagement)

        # ---- persist ----
        computed_at = now.isoformat()
        profile = Learning_Profile(
            pace=pace,
            preferred_style=preferred_style,
            topic_signals=topic_signals,
            computed_at=computed_at,
        )
        profile_json = serialize_profile(profile)

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_learning_profiles
                    (user_id, profile_json, computed_at, stale)
                VALUES (?, ?, ?, 0)
                """,
                (user_id, profile_json, computed_at),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.error("Personalization_Engine.compute_profile persist failed: %s", exc)

        return profile

    # ------------------------------------------------------------------
    # get_or_refresh_profile
    # ------------------------------------------------------------------

    def get_or_refresh_profile(self, conn: sqlite3.Connection, user_id: int) -> Learning_Profile:
        """Return the cached profile or recompute it when appropriate.

        Rules:
        - No row → compute and return.
        - stale=0 → return cached.
        - stale=1 AND computed_at < 30 min ago → return cached (throttle).
        - stale=1 AND computed_at >= 30 min ago → recompute.
        """
        row = conn.execute(
            "SELECT profile_json, computed_at, stale FROM user_learning_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            return self.compute_profile(conn, user_id)

        profile_json, computed_at_str, stale = row

        if stale == 0:
            return deserialize_profile(profile_json)

        # stale=1 — check age
        try:
            computed_at_dt = datetime.fromisoformat(computed_at_str)
            if computed_at_dt.tzinfo is None:
                computed_at_dt = computed_at_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # Unparseable timestamp — recompute to be safe
            return self.compute_profile(conn, user_id)

        now = datetime.now(timezone.utc)
        age_minutes = (now - computed_at_dt).total_seconds() / 60.0

        if age_minutes < 30:
            # Within throttle window — return cached even though stale
            return deserialize_profile(profile_json)

        return self.compute_profile(conn, user_id)

    # ------------------------------------------------------------------
    # compute_pace_suggestion
    # ------------------------------------------------------------------

    def compute_pace_suggestion(
        self, conn: sqlite3.Connection, user_id: int, roadmap_id: int
    ) -> None:
        """Generate a pace adjustment suggestion when computed pace diverges > 30%.

        Skips insertion if:
        - An undismissed suggestion already exists for this user+roadmap.
        - A suggestion was dismissed within the last 7 days.
        """
        # Get weekly_hours from user_roadmap
        row = conn.execute(
            "SELECT weekly_hours FROM user_roadmap WHERE user_id = ? AND id = ?",
            (user_id, roadmap_id),
        ).fetchone()
        if row is None or row[0] is None:
            return
        weekly_hours = float(row[0])

        # Get computed pace
        profile = self.get_or_refresh_profile(conn, user_id)
        computed_pace = profile.pace

        # Implied pace: weekly_hours / 2.0 (assume 2 hours per topic)
        implied_pace = weekly_hours / 2.0

        # Check divergence
        divergence = abs(computed_pace - implied_pace) / max(implied_pace, 0.001)
        if divergence <= 0.30:
            return

        now = datetime.now(timezone.utc)

        # Check for existing undismissed suggestion
        undismissed = conn.execute(
            """
            SELECT id FROM pace_suggestions
            WHERE user_id = ? AND roadmap_id = ? AND dismissed = 0
            """,
            (user_id, roadmap_id),
        ).fetchone()
        if undismissed:
            return

        # Check for suggestion dismissed within last 7 days
        seven_days_ago = datetime.fromtimestamp(
            now.timestamp() - 7 * 24 * 3600, tz=timezone.utc
        ).isoformat()
        recently_dismissed = conn.execute(
            """
            SELECT id FROM pace_suggestions
            WHERE user_id = ? AND roadmap_id = ? AND dismissed = 1
              AND created_at >= ?
            """,
            (user_id, roadmap_id, seven_days_ago),
        ).fetchone()
        if recently_dismissed:
            return

        # Insert suggestion
        suggested_weekly_hours = round(computed_pace * 2)
        reason = (
            f"You're completing {computed_pace:.1f} topics/week but your goal assumes "
            f"{implied_pace:.1f} — consider adjusting to {suggested_weekly_hours} hrs/week."
        )
        try:
            conn.execute(
                """
                INSERT INTO pace_suggestions
                    (user_id, roadmap_id, suggested_weekly_hours, reason, created_at, dismissed)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (user_id, roadmap_id, suggested_weekly_hours, reason, now.isoformat()),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.error("Personalization_Engine.compute_pace_suggestion insert failed: %s", exc)


# ---------------------------------------------------------------------------
# RecommendedItem dataclass
# ---------------------------------------------------------------------------

@dataclass
class RecommendedItem:
    """A single topic recommendation with scoring metadata."""
    topic: str
    roadmap_id: int
    path_name: str
    score: float        # 0.0–1.0
    explanation: str    # 10–30 words


# ---------------------------------------------------------------------------
# Recommendation Service
# ---------------------------------------------------------------------------

class Recommendation_Service:
    """Scores and ranks incomplete roadmap topics for a given user."""

    def get_recommendations(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        roadmap_id: int,
        limit: int = 5,
    ) -> list[RecommendedItem]:
        """Return up to `limit` recommended incomplete topics, scored by composite formula.

        Scoring formula:
            composite = 0.4 * prerequisite_readiness
                      + 0.3 * engagement_potential
                      + 0.2 * difficulty_fit
                      + 0.1 * recency_boost

        Fallback: if no behavioral history (empty topic_signals AND no completed topics),
        return first `limit` incomplete topics in roadmap order with score=0.0.
        """
        # --- fetch roadmap ---
        row = conn.execute(
            "SELECT roadmap_json, name FROM user_roadmap WHERE user_id = ? AND id = ?",
            (user_id, roadmap_id),
        ).fetchone()
        if row is None:
            return []

        roadmap_json_str, path_name = row
        try:
            weeks: list[dict] = json.loads(roadmap_json_str)
        except (json.JSONDecodeError, TypeError):
            return []

        # --- completed topics ---
        completed_rows = conn.execute(
            "SELECT topic FROM progress WHERE user_id = ? AND roadmap_id = ? AND status = 'completed'",
            (user_id, roadmap_id),
        ).fetchall()
        completed_topics: set[str] = {r[0] for r in completed_rows}

        # --- profile ---
        profile = Personalization_Engine().get_or_refresh_profile(conn, user_id)

        # --- weekly focus topics (current week starting Monday) ---
        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()  # Monday=0
        week_start = datetime.fromtimestamp(
            now.timestamp() - days_since_monday * 86400, tz=timezone.utc
        ).strftime("%Y-%m-%d")
        focus_rows = conn.execute(
            "SELECT topic FROM weekly_focus WHERE user_id = ? AND week_start >= ?",
            (user_id, week_start),
        ).fetchall()
        weekly_focus_topics: set[str] = {r[0] for r in focus_rows}

        # --- all topics in order ---
        all_topics: list[str] = [w["topic"] for w in weeks if "topic" in w]
        incomplete_topics: list[str] = [t for t in all_topics if t not in completed_topics]

        if not incomplete_topics:
            return []

        # --- fallback: no behavioral history ---
        has_history = bool(profile.topic_signals) or bool(completed_topics)
        if not has_history:
            return [
                RecommendedItem(
                    topic=t,
                    roadmap_id=roadmap_id,
                    path_name=path_name,
                    score=0.0,
                    explanation="Start here to begin your personalized learning journey with this foundational topic.",
                )
                for t in incomplete_topics[:limit]
            ]

        # --- user level from pace ---
        pace = profile.pace
        if pace < 1.5:
            user_level_range = (1, 2)   # beginner
        elif pace < 3.0:
            user_level_range = (3, 3)   # intermediate
        else:
            user_level_range = (4, 5)   # advanced

        scored: list[tuple[float, str, str]] = []  # (score, topic, explanation)

        for topic in incomplete_topics:
            topic_idx = all_topics.index(topic)
            predecessors = all_topics[:topic_idx]

            # --- prerequisite_readiness ---
            if not predecessors:
                prerequisite_readiness = 1.0
            else:
                completed_before = sum(1 for p in predecessors if p in completed_topics)
                ratio = completed_before / len(predecessors)
                if ratio >= 1.0:
                    prerequisite_readiness = 1.0
                elif ratio >= 0.5:
                    prerequisite_readiness = 0.5
                else:
                    prerequisite_readiness = 0.0

            # --- engagement_potential ---
            topic_words = set(topic.lower().split())
            best_overlap = -1
            best_engagement = 0.5
            for completed_t in completed_topics:
                if completed_t in profile.topic_signals:
                    overlap = len(topic_words & set(completed_t.lower().split()))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_engagement = profile.topic_signals[completed_t].engagement
            engagement_potential = best_engagement

            # --- difficulty_fit ---
            if topic in profile.topic_signals:
                topic_difficulty = profile.topic_signals[topic].difficulty
            else:
                topic_difficulty = 3  # default

            level_lo, level_hi = user_level_range
            if level_lo <= topic_difficulty <= level_hi:
                difficulty_fit = 1.0
            elif abs(topic_difficulty - level_lo) == 1 or abs(topic_difficulty - level_hi) == 1:
                difficulty_fit = 0.5
            else:
                difficulty_fit = 0.0

            # --- recency_boost ---
            recency_boost = 1.0 if topic in weekly_focus_topics else 0.0

            # --- composite ---
            composite = (
                0.4 * prerequisite_readiness
                + 0.3 * engagement_potential
                + 0.2 * difficulty_fit
                + 0.1 * recency_boost
            )

            # --- explanation (based on highest-scoring component) ---
            components = {
                "prerequisite_readiness": (0.4 * prerequisite_readiness, prerequisite_readiness),
                "engagement_potential": (0.3 * engagement_potential, engagement_potential),
                "difficulty_fit": (0.2 * difficulty_fit, difficulty_fit),
                "recency_boost": (0.1 * recency_boost, recency_boost),
            }
            top_component = max(components, key=lambda k: components[k][0])

            if top_component == "prerequisite_readiness":
                if prerequisite_readiness == 1.0:
                    explanation = "You have completed all prerequisite topics and are fully ready to tackle this next step in your learning path."
                else:
                    explanation = "You have completed enough prerequisites to make solid progress on this topic right now."
            elif top_component == "engagement_potential":
                explanation = "Based on your past engagement with similar topics, you are likely to find this topic highly interesting and rewarding."
            elif top_component == "difficulty_fit":
                explanation = "This topic matches your current skill level well, making it an ideal challenge for your learning pace."
            else:  # recency_boost
                explanation = "This topic is part of your weekly focus plan, making it a timely and relevant choice for this week."

            scored.append((composite, topic, explanation))

        # --- sort descending by score ---
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            RecommendedItem(
                topic=topic,
                roadmap_id=roadmap_id,
                path_name=path_name,
                score=round(score, 4),
                explanation=explanation,
            )
            for score, topic, explanation in scored[:limit]
        ]


# ---------------------------------------------------------------------------
# Roadmap Reordering
# ---------------------------------------------------------------------------

def reorder_roadmap(
    topics: list[dict],
    completed_set: set[str],
    recommendation_scores: dict[str, float],
    manual_order_set: set[str],
    paused: bool,
) -> list[dict]:
    """Reorder roadmap topics according to personalization rules.

    Args:
        topics: List of topic dicts, each with at minimum {"topic": str, "week": int}.
                May also contain "manual_order": bool.
        completed_set: Set of topic names that are already completed.
        recommendation_scores: Mapping of topic name -> composite score (higher = more recommended).
        manual_order_set: Set of topic names that must keep their current positions.
        paused: If True, return topics sorted by week ascending (original order).

    Returns:
        Reordered list of topic dicts.

    Rules:
        1. If paused=True: return topics sorted by week ascending, ignoring scores.
        2. Otherwise:
           - Topics in manual_order_set OR where topic dict has manual_order=True
             keep their current (original) positions.
           - Incomplete non-manual topics: sorted by recommendation_scores descending.
           - Completed non-manual topics: sorted by week ascending.
           - Manual topics are interleaved at their original index positions.
    """
    # Rule 1: paused — return sorted by week ascending
    if paused:
        return sorted(topics, key=lambda t: t.get("week", 0))

    # Classify each topic by its original index
    manual_indices: dict[int, dict] = {}   # original_index -> topic dict
    incomplete_non_manual: list[dict] = []
    completed_non_manual: list[dict] = []

    for idx, topic_dict in enumerate(topics):
        topic_name = topic_dict.get("topic", "")
        is_manual = topic_name in manual_order_set or topic_dict.get("manual_order", False)

        if is_manual:
            manual_indices[idx] = topic_dict
        elif topic_name in completed_set:
            completed_non_manual.append(topic_dict)
        else:
            incomplete_non_manual.append(topic_dict)

    # Sort incomplete non-manual by score descending
    incomplete_non_manual.sort(
        key=lambda t: recommendation_scores.get(t.get("topic", ""), 0.0),
        reverse=True,
    )

    # Sort completed non-manual by week ascending
    completed_non_manual.sort(key=lambda t: t.get("week", 0))

    # Build result: fill slots left-to-right; manual topics occupy their original indices
    fill_queue: list[dict] = incomplete_non_manual + completed_non_manual
    fill_iter = iter(fill_queue)

    result: list[dict] = []
    for idx in range(len(topics)):
        if idx in manual_indices:
            result.append(manual_indices[idx])
        else:
            try:
                result.append(next(fill_iter))
            except StopIteration:
                break  # should not happen if inputs are consistent

    return result
