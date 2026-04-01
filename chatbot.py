"""
Groq-powered chatbot for the AI Learning Platform.
Modes: general, path_creation, research.
Falls back to rule-based responses if Groq is unavailable.
"""

import json
import re
from pathlib import Path

from generator import _groq_chat, load_json

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

SYSTEM_GENERAL = """You are an expert AI learning assistant for an AI Learning Platform.
Help users with:
- Learning path advice and topic explanations
- Study strategies and resources
- AI/ML/Data Science/Web Dev concepts
- Career guidance in tech

Be concise, friendly, and practical. Use markdown for formatting when helpful.
Keep responses under 200 words unless a detailed explanation is needed."""

SYSTEM_RESEARCH = """You are a tech career research expert specializing in 2024-2025 AI/ML job market.
Provide accurate information about:
- Emerging AI roles and their responsibilities
- Required skills and tech stacks
- Realistic salary ranges (USD)
- Career progression paths
- In-demand certifications

Be specific with numbers and current trends. Keep responses concise and actionable."""

SYSTEM_PATH = """You are a learning path advisor helping users build personalized study roadmaps.
Guide users through collecting: domain, current level, target role, known skills.
Once you have all info, summarize the plan and tell them to use the Generate Path page.
Be conversational and encouraging. Ask one question at a time."""


def _load_emerging_roles() -> list:
    p = DATA_DIR / "emerging_roles.json"
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _groq_reply(system: str, message: str, history: list = None) -> str:
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-6:])  # last 3 exchanges for context
    messages.append({"role": "user", "content": message})
    return _groq_chat(messages, temperature=0.6)


# ── Fallback rule-based replies ──────────────────────────────────────────────

def _fallback_general(message: str) -> str:
    m = message.lower()
    if any(w in m for w in ["hello", "hi", "hey"]):
        return "Hi! I can help with learning paths, AI concepts, and career advice. What would you like to know?"
    if "help" in m:
        return "Use **General** for questions, **Path Creation** to plan a roadmap, or **Research** for roles and salaries."
    return "I'm here to help with your learning journey. Ask me about topics, paths, or career advice."


def _fallback_research(message: str) -> str:
    roles = _load_emerging_roles()
    m = message.lower()
    if "list" in m or "all" in m:
        return "**2025 AI Roles:**\n" + "\n".join(f"• **{r['role']}** — {r['salary_range']}" for r in roles)
    for r in roles:
        if r["role"].lower() in m:
            return f"**{r['role']}**\nSkills: {r['skills']}\nSalary: {r['salary_range']}"
    return "Ask about a specific role (e.g. 'ML Engineer'), 'salaries', or 'list roles'."


def _fallback_path(message: str, context: dict) -> tuple:
    m = message.lower()
    if not context.get("domain"):
        for d in ["ai", "data science", "web development", "cloud computing"]:
            if d in m:
                context["domain"] = d.title()
                return f"Got it — **{context['domain']}**. What's your current level? (Beginner / Intermediate / Advanced)", context
        return "What **domain** do you want to learn? (AI, Data Science, Web Development, Cloud Computing)", context
    if not context.get("level"):
        for l in ["beginner", "intermediate", "advanced"]:
            if l in m:
                context["level"] = l
                return f"**{l.capitalize()}** noted. What's your **target role**?", context
        return "What's your current level? (Beginner / Intermediate / Advanced)", context
    if not context.get("target_role"):
        context["target_role"] = message.strip() or "Learner"
        return f"Target role: **{context['target_role']}**. Any known skills to skip? (or say 'none')", context
    context["known_skills"] = [s.strip() for s in re.split(r"[,;]", message) if s.strip() and message.lower() != "none"]
    return (f"Ready! Head to **Generate Path** and use:\n- Domain: {context['domain']}\n"
            f"- Level: {context['level']}\n- Role: {context['target_role']}\n"
            f"to create your personalized roadmap."), context


# ── Main handler ─────────────────────────────────────────────────────────────

def handle_chat(message: str, mode: str, context: dict = None) -> dict:
    context = context or {}
    history = context.get("history", [])

    if mode == "general":
        try:
            reply = _groq_reply(SYSTEM_GENERAL, message, history)
        except Exception:
            reply = _fallback_general(message)
        new_history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        return {"reply": reply, "context": {"history": new_history[-10:]}}

    if mode == "research":
        # Enrich with local role data as context
        roles = _load_emerging_roles()
        roles_ctx = json.dumps(roles[:10], ensure_ascii=False) if roles else ""
        system = SYSTEM_RESEARCH + (f"\n\nAvailable role data:\n{roles_ctx}" if roles_ctx else "")
        try:
            reply = _groq_reply(system, message, history)
        except Exception:
            reply = _fallback_research(message)
        new_history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        return {"reply": reply, "context": {"history": new_history[-10:]}}

    if mode == "path_creation":
        try:
            ctx_summary = ""
            if context.get("domain"):
                ctx_summary = f"Collected so far — Domain: {context.get('domain')}, Level: {context.get('level','?')}, Role: {context.get('target_role','?')}"
            system = SYSTEM_PATH + (f"\n\n{ctx_summary}" if ctx_summary else "")
            reply = _groq_reply(system, message, history)
        except Exception:
            reply, context = _fallback_path(message, context)
            return {"reply": reply, "context": context}
        new_history = history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        context["history"] = new_history[-10:]
        return {"reply": reply, "context": context}

    return {"reply": "Unknown mode.", "context": {}}
