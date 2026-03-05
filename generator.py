"""
AI Personalized Learning Platform - Recommendation Engine.

Loads topics from knowledge_base.json, filters by level and known skills,
and generates a weekly learning plan with tasks and resources from resources.json.
"""

import json
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    """Return the directory containing this script (project root)."""
    return Path(__file__).resolve().parent


def load_json(filename: str, base_dir: Path | None = None) -> dict:
    """Load a JSON file. If base_dir is given, filename is path from base_dir; else from project root."""
    root = base_dir if base_dir is not None else _project_root()
    path = (root / filename) if not Path(filename).is_absolute() else Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _allowed_levels(current_level: str) -> list[str]:
    """
    Map user's current level to allowed topic levels.
    Beginner -> beginner + intermediate; Intermediate -> intermediate + advanced; Advanced -> advanced only.
    """
    level = current_level.strip().lower()
    if level == "beginner":
        return ["beginner", "intermediate"]
    if level == "intermediate":
        return ["intermediate", "advanced"]
    if level == "advanced":
        return ["advanced"]
    return ["beginner", "intermediate", "advanced"]


def _normalize_known_skills(known_skills: list[str]) -> set[str]:
    """Normalize known skills for case-insensitive matching."""
    return {s.strip().lower() for s in known_skills if s.strip()}


# Default tasks per topic (can be overridden or extended via config later).
DEFAULT_TASKS = [
    "Watch tutorial or read documentation",
    "Solve practice problems",
    "Build a small project or exercise",
]


def get_tasks_for_topic(topic_name: str) -> list[str]:
    """Return a list of tasks for a topic. Uses defaults if no custom mapping exists."""
    return list(DEFAULT_TASKS)


def generate_roadmap(
    domain: str,
    current_level: str,
    weekly_study_hours: int,
    known_skills: list[str],
    knowledge_base_path: str = "knowledge_base.json",
    resources_path: str = "resources.json",
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Generate a structured weekly learning roadmap.

    Steps:
    1. Load knowledge base and resources.
    2. Filter topics by domain and allowed levels.
    3. Remove topics that appear in known_skills.
    4. Build one week per topic with topic name, tasks, and learning resources.

    Returns a list of week dicts: [{"week": 1, "topic": str, "tasks": [...], "resources": [...]}, ...]
    """
    root = base_dir if base_dir is not None else _project_root()
    kb = load_json(knowledge_base_path, base_dir=root)
    resources_map = load_json(resources_path, base_dir=root)

    domain_key = domain.strip()
    if domain_key not in kb:
        raise ValueError(f"Domain '{domain}' not found in knowledge base.")

    topics = kb[domain_key]
    allowed = set(_allowed_levels(current_level))
    known = _normalize_known_skills(known_skills)

    filtered = [
        t
        for t in topics
        if t.get("level", "").lower() in allowed
        and t.get("topic", "").strip().lower() not in known
    ]

    roadmap = []
    for i, t in enumerate(filtered, start=1):
        topic_name = (t.get("topic") or "").strip()
        tasks = get_tasks_for_topic(topic_name)
        resource_urls = resources_map.get(topic_name) or []

        roadmap.append({
            "week": i,
            "topic": topic_name,
            "tasks": tasks,
            "resources": resource_urls,
        })

    return roadmap
