"""
AI Personalized Learning Platform - Recommendation Engine.
Uses Groq LLM for fully dynamic, personalized roadmap generation.
No static datasets. Every roadmap is generated fresh from user input.
Falls back to knowledge-base only if API is unavailable.
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


def _groq_chat(messages: list[dict], model: str = "llama-3.3-70b-versatile", temperature: float = 0.5) -> str:
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


def _weeks_from_hours(weekly_study_hours: int, level: str) -> int:
    """Determine roadmap length based on hours/week and level."""
    base = {"beginner": 14, "intermediate": 10, "advanced": 8}.get(level.lower(), 10)
    if weekly_study_hours <= 3:
        return base + 4
    elif weekly_study_hours <= 8:
        return base
    else:
        return max(6, base - 3)


def _generate_roadmap_groq(
    domain: str,
    current_level: str,
    target_role: str,
    weekly_study_hours: int,
    known_skills: list[str],
    learning_style: str = "balanced",
    goal_date: str = "",
) -> list[dict[str, Any]]:
    """
    Fully dynamic AI roadmap generation using Groq.
    No static datasets — every roadmap is unique to the user's input.
    """
    num_weeks = _weeks_from_hours(weekly_study_hours, current_level)
    known_str = ", ".join(known_skills) if known_skills else "none"
    deadline_str = f"Target completion date: {goal_date}" if goal_date else "No fixed deadline"

    level_context = {
        "beginner": "complete beginner — start from absolute fundamentals, assume no prior knowledge in this domain",
        "intermediate": "intermediate learner — skip basics, focus on deeper concepts, design patterns, and real projects",
        "advanced": "advanced practitioner — skip fundamentals entirely, focus on architecture, optimization, and production-grade work",
    }.get(current_level.lower(), current_level)

    style_context = {
        "practical": "hands-on coding and project-based tasks — minimize pure theory",
        "visual": "video tutorials, diagrams, and visual walkthroughs",
        "theoretical": "deep reading, papers, documentation, and conceptual mastery",
        "balanced": "mix of theory, coding exercises, and mini-projects each week",
    }.get(learning_style.lower(), "balanced mix of theory and practice")

    system_prompt = """You are an expert AI learning path generator running on a Groq-powered LLM.
Your task is to dynamically generate a personalized learning roadmap WITHOUT using any predefined datasets, static files, or memorized lists.

IMPORTANT RULES:
- Do NOT rely on or assume any static knowledge base
- Do NOT reuse fixed topic sequences
- Generate the roadmap dynamically based ONLY on the user input
- Ensure variation and personalization in every response
- Return ONLY valid JSON — no explanations, no markdown, no extra text"""

    user_prompt = f"""Generate a fully personalized learning roadmap for:

Domain: {domain}
Current Level: {level_context}
Target Role: {target_role}
Weekly Study Hours: {weekly_study_hours} hours/week
Learning Style: {style_context}
Skills to SKIP (already known): {known_str}
Duration: {num_weeks} weeks
{deadline_str}

Instructions:
1. Create a fully dynamic, customized learning path — NOT a generic sequence
2. Ensure logical progression from the learner's current level toward {target_role}
3. Adapt topics specifically to {target_role} — not generic domain topics
4. Avoid repeating common static sequences unless truly justified by the role
5. Make each week unique and context-aware
6. Include a mix of: theory, coding, project (adapt ratio to learning style)
7. Each topic must be specific (e.g. "Transformer Architecture for NLP" not just "Deep Learning")

Return ONLY a valid JSON array with exactly {num_weeks} weeks:
[
  {{
    "week": 1,
    "topic": "Specific Topic Name",
    "type": "theory|coding|project",
    "description": "Clear explanation of what to learn and how",
    "outcome": "Concrete skill or artifact the learner will have after this week",
    "tasks": ["Actionable task 1", "Actionable task 2", "Actionable task 3"],
    "resources": ["https://real-url-1.com", "https://real-url-2.com"]
  }}
]

CRITICAL: Return ONLY the JSON array. No markdown. No explanation. No code fences."""

    content = _groq_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.6)

    # Clean response
    content = content.strip()
    content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'\s*```\s*$', '', content, flags=re.MULTILINE)
    content = content.strip()

    # Extract JSON array
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if match:
        content = match.group(0)

    roadmap = json.loads(content)

    if not isinstance(roadmap, list) or not roadmap:
        raise ValueError("Invalid roadmap structure from AI")

    # Normalize and validate each week
    for i, week in enumerate(roadmap):
        if not isinstance(week, dict):
            raise ValueError(f"Week {i} is not a dict")
        week["week"] = week.get("week", i + 1)
        week.setdefault("topic", f"Week {i + 1} Topic")
        week.setdefault("type", "theory")
        week.setdefault("description", "Study this topic in depth.")
        week.setdefault("outcome", "Understand and apply the concepts.")
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
    Uses Groq AI for fully dynamic generation; falls back to knowledge-base.
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
            "type": "theory",
            "description": f"Study {(t.get('topic') or '').strip()} in depth.",
            "outcome": f"Understand and apply {(t.get('topic') or '').strip()}.",
            "tasks": list(DEFAULT_TASKS),
            "resources": resources_map.get((t.get("topic") or "").strip(), []),
        }
        for i, t in enumerate(filtered, start=1)
    ]
