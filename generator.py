"""
AI Personalized Learning Platform - Recommendation Engine.
Uses Groq LLM for deeply personalized roadmap generation based on user experience.
Falls back to knowledge-base if API unavailable.
"""

import json
import os
import re
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def load_json(filename: str, base_dir: Path | None = None) -> dict:
    root = base_dir if base_dir is not None else _project_root()
    path = (root / filename) if not Path(filename).is_absolute() else Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_groq_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        env_path = _project_root() / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("GROQ_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    return key


def _groq_chat(messages: list[dict], model: str = "llama-3.3-70b-versatile", temperature: float = 0.4) -> str:
    import requests as _req
    key = _get_groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    resp = _req.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": 4096},
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _build_experience_profile(
    current_level: str,
    known_skills: list[str],
    weekly_study_hours: int,
    learning_style: str,
    goal_date: str,
) -> str:
    """Build a rich experience profile string for the AI prompt."""
    level_desc = {
        "beginner": "complete beginner with little to no prior experience in this domain",
        "intermediate": "intermediate learner who understands fundamentals and has built small projects",
        "advanced": "advanced practitioner with professional experience looking to deepen expertise",
    }.get(current_level.lower(), current_level)

    style_desc = {
        "visual": "learns best through videos, diagrams, and visual demonstrations",
        "practical": "learns best by building projects and hands-on coding exercises",
        "theoretical": "learns best through reading documentation, papers, and conceptual understanding",
        "balanced": "prefers a mix of theory and practice",
    }.get(learning_style.lower(), "prefers a balanced mix of theory and practice")

    hours_desc = (
        f"{weekly_study_hours} hours/week"
        + (" (very limited time — keep topics focused)" if weekly_study_hours <= 3
           else " (moderate time — good balance of depth and breadth)" if weekly_study_hours <= 8
           else " (significant time — can go deep on each topic)")
    )

    skills_str = ", ".join(known_skills) if known_skills else "none listed"
    deadline_str = f"Target completion: {goal_date}" if goal_date else "No fixed deadline"

    return f"""Learner Profile:
- Experience: {level_desc}
- Known skills to SKIP: {skills_str}
- Study time: {hours_desc}
- Learning style: {style_desc}
- {deadline_str}"""


def _generate_roadmap_groq(
    domain: str,
    current_level: str,
    target_role: str,
    weekly_study_hours: int,
    known_skills: list[str],
    learning_style: str = "balanced",
    goal_date: str = "",
) -> list[dict[str, Any]]:
    """Use Groq to generate a deeply personalized weekly roadmap as JSON."""

    profile = _build_experience_profile(
        current_level, known_skills, weekly_study_hours, learning_style, goal_date
    )

    # Adapt week count to available time and level
    if weekly_study_hours <= 3:
        week_range = "10 to 14"
    elif weekly_study_hours <= 8:
        week_range = "8 to 12"
    else:
        week_range = "6 to 10"

    prompt = f"""You are an expert AI learning path designer who creates deeply personalized roadmaps.

GOAL: Create a weekly learning roadmap for someone who wants to become a {target_role} in {domain}.

{profile}

INSTRUCTIONS:
1. Analyze the learner's current level and known skills carefully
2. Start exactly where they are — don't repeat what they already know
3. Progress logically from their current level toward {target_role} skills
4. Adapt task types to their learning style
5. Keep weekly workload realistic for {weekly_study_hours} hours/week
6. Include {week_range} weeks total

Return ONLY a valid JSON array. Each element must be exactly:
{{"week": <int>, "topic": "<specific topic>", "tasks": ["<actionable task 1>", "<actionable task 2>", "<actionable task 3>"], "resources": ["<real URL 1>", "<real URL 2>"]}}

Requirements for each week:
- topic: specific and concrete (e.g. "Neural Networks with PyTorch" not just "Deep Learning")
- tasks: 3 actionable items matching the learner's style (build/watch/read/practice)
- resources: 2 real, working URLs (YouTube tutorials, official docs, GitHub repos, Coursera, fast.ai, etc.)
- Progressive difficulty — each week builds on the previous
- NO markdown, NO explanation, ONLY the JSON array"""

    content = _groq_chat([
        {"role": "system", "content": "You are a JSON-only learning path generator. You output only valid JSON arrays, nothing else. No markdown code blocks, no explanation."},
        {"role": "user", "content": prompt},
    ], temperature=0.3)

    content = content.strip()
    # Strip markdown code fences if present
    content = re.sub(r'^```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```$', '', content)
    # Extract JSON array
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if match:
        content = match.group(0)
    roadmap = json.loads(content)
    # Validate structure
    if not isinstance(roadmap, list) or not roadmap:
        raise ValueError("Invalid roadmap structure from AI")
    for i, week in enumerate(roadmap):
        if not isinstance(week, dict) or "topic" not in week:
            raise ValueError(f"Invalid week structure at index {i}")
        week.setdefault("week", i + 1)
        week.setdefault("tasks", ["Study the topic", "Practice with examples", "Build a mini project"])
        week.setdefault("resources", [])
    return roadmap


def _allowed_levels(current_level: str) -> list[str]:
    level = current_level.strip().lower()
    if level == "beginner":
        return ["beginner", "intermediate"]
    if level == "intermediate":
        return ["intermediate", "advanced"]
    if level == "advanced":
        return ["advanced"]
    return ["beginner", "intermediate", "advanced"]


def _normalize_known_skills(known_skills: list[str]) -> set[str]:
    return {s.strip().lower() for s in known_skills if s.strip()}


DEFAULT_TASKS = [
    "Watch tutorial or read documentation",
    "Solve practice problems",
    "Build a small project or exercise",
]


def generate_roadmap(
    domain: str,
    current_level: str,
    weekly_study_hours: int,
    known_skills: list[str],
    target_role: str = "Learner",
    learning_style: str = "balanced",
    goal_date: str = "",
    knowledge_base_path: str = "knowledge_base.json",
    resources_path: str = "resources.json",
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Generate a personalized weekly learning roadmap.
    Uses Groq AI for deep personalization; falls back to knowledge-base.
    """
    try:
        roadmap = _generate_roadmap_groq(
            domain=domain,
            current_level=current_level,
            target_role=target_role,
            weekly_study_hours=weekly_study_hours,
            known_skills=known_skills,
            learning_style=learning_style,
            goal_date=goal_date,
        )
        if roadmap:
            return roadmap
    except Exception:
        pass  # Fall through to knowledge-base fallback

    # Fallback: knowledge-base
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
        t for t in topics
        if t.get("level", "").lower() in allowed
        and t.get("topic", "").strip().lower() not in known
    ]

    return [
        {
            "week": i,
            "topic": (t.get("topic") or "").strip(),
            "tasks": list(DEFAULT_TASKS),
            "resources": resources_map.get((t.get("topic") or "").strip(), []),
        }
        for i, t in enumerate(filtered, start=1)
    ]
