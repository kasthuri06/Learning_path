"""
Rule-based chatbot for the AI Learning Platform.
Modes: general (Q&A), path_creation (conversational path generation), research (skills/salaries/roles).
No external API; uses knowledge_base, resources, and static data.
"""

import json
import re
from pathlib import Path

from generator import generate_roadmap, load_json

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"


def _load_emerging_roles() -> list:
    p = DATA_DIR / "emerging_roles.json"
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_kb_resources():
    try:
        kb = load_json("knowledge_base.json", base_dir=APP_DIR)
        res = load_json("resources.json", base_dir=APP_DIR)
        return kb, res
    except Exception:
        return {}, {}


def _general_reply(message: str) -> str:
    m = message.lower().strip()
    if not m:
        return "Ask me anything about learning paths, topics, or resources."
    if "hello" in m or "hi " in m or m == "hi":
        return "Hi! I can help with learning paths, path creation, or research on skills and roles. What would you like to do?"
    if "help" in m:
        return "Use **General Chat** for questions, **Path Creation** to build a learning path by conversation, or **Research** to explore 2025 AI roles, skills, and salary ranges."
    if "path" in m or "roadmap" in m or "learn" in m:
        return "Switch to **Path Creation** mode and tell me your domain (e.g. AI, Data Science), your level, and target role. I'll build a custom roadmap for you."
    if "resource" in m or "link" in m or "video" in m:
        return "After you generate a path, each topic on your roadmap includes curated resources (videos, docs). You can track them with checkboxes."
    return "I'm a rule-based assistant. For detailed path generation use Path Creation; for roles and salaries use Research."


def _research_reply(message: str) -> str:
    roles = _load_emerging_roles()
    m = message.lower().strip()
    if not m:
        return "Ask about a role (e.g. 'ML Engineer'), 'salaries', 'skills', or 'list roles' for 2025 emerging AI roles."
    if "list" in m or ("role" in m and "all" in m) or "every" in m:
        lines = [f"• **{r['role']}** — {r['salary_range']}" for r in roles]
        return "**2025 emerging AI roles:**\n\n" + "\n".join(lines)
    if "salar" in m or "pay" in m or "salary" in m:
        lines = [f"• {r['role']}: {r['salary_range']}" for r in roles]
        return "**Salary ranges (typical):**\n\n" + "\n".join(lines)
    for r in roles:
        if r["role"].lower() in m or m in r["role"].lower():
            return f"**{r['role']}**\nSkills: {r['skills']}\nSalary range: {r['salary_range']}"
    if "skill" in m:
        return "Roles and their key skills are in the collapsible **Skills database** on the Generate page, or ask me for a specific role by name in Research mode."
    return "Try: 'ML Engineer', 'Prompt Engineer', 'salaries', or 'list roles'."


def _path_creation_reply(message: str, context: dict) -> tuple:
    m = message.lower().strip()
    domain_set = {"ai", "data science", "web development", "cloud computing"}
    level_set = {"beginner", "intermediate", "advanced"}

    if not context:
        context = {}

    domain = context.get("domain")
    level = context.get("level")
    target_role = context.get("target_role")
    known_skills = context.get("known_skills") or []

    if domain and level and target_role:
        try:
            roadmap = generate_roadmap(
                domain=domain,
                current_level=level.capitalize(),
                weekly_study_hours=context.get("weekly_hours", 5),
                known_skills=known_skills,
                knowledge_base_path=str(APP_DIR / "knowledge_base.json"),
                resources_path=str(APP_DIR / "resources.json"),
                base_dir=APP_DIR,
            )
            topics = [w["topic"] for w in roadmap]
            summary = "Your path is ready! Topics: " + ", ".join(topics[:5])
            if len(topics) > 5:
                summary += f" … and {len(topics) - 5} more."
            summary += " Go to **Generate Path** and click Generate to save it to your dashboard."
            return summary, {"roadmap_preview": roadmap}
        except Exception as e:
            return f"I couldn't build the path: {e}. Try again with domain, level, and role.", context

    if not domain:
        for d in domain_set:
            if d in m:
                context["domain"] = d.title()
                return f"Got it, **{context['domain']}**. What's your current level? (Beginner, Intermediate, Advanced)", context
        return "What **domain** do you want to learn? (AI, Data Science, Web Development, Cloud Computing)", context

    if not level:
        for l in level_set:
            if l in m:
                context["level"] = l
                return f"**{l.capitalize()}** — great. What's your **target role**? (e.g. ML Engineer, Data Scientist)", context
        return "What's your **current level**? (Beginner, Intermediate, Advanced)", context

    if not target_role:
        role = message.strip() or "Learner"
        context["target_role"] = role
        context["weekly_hours"] = 5
        return f"Target role **{role}** saved. Any **known skills** to skip? (comma-separated, or say 'none'). Then I'll generate your path.", context

    if "none" not in m and "no " not in m and "skip" not in m and m:
        context["known_skills"] = [s.strip() for s in re.split(r"[,;]", message) if s.strip()]

    return _path_creation_reply("generate", context)


def handle_chat(message: str, mode: str, context: dict = None) -> dict:
    context = context or {}
    if mode == "general":
        return {"reply": _general_reply(message), "context": {}}
    if mode == "research":
        return {"reply": _research_reply(message), "context": {}}
    if mode == "path_creation":
        reply, new_ctx = _path_creation_reply(message, context)
        return {"reply": reply, "context": new_ctx}
    return {"reply": "Unknown mode. Use general, path_creation, or research.", "context": {}}
