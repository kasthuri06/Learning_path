# AI Personalized Learning Platform

A lightweight, production-style learning platform with authentication, dashboard analytics, and an AI-style personalized roadmap generator. Similar to a simplified LeetCode-style learning dashboard.

## Tech stack

- **Backend:** Python + Flask  
- **Frontend:** HTML + Bootstrap 5 + Jinja2  
- **Database:** SQLite  
- **Charts:** Chart.js  
- **Data:** JSON knowledge base + resources

## Features

- **User authentication:** Sign up, log in, log out (Flask sessions)
- **Dashboard:** Total topics, completed topics, progress %, learning streak; Chart.js for weekly progress and completion %
- **Generate Learning Path:** Domain, level, target role, weekly hours, known skills → personalized weekly roadmap
- **Roadmap page:** Week-by-week view with topic, tasks, resources, and “Mark as Completed”
- **Progress tracking:** SQLite `progress` table (user_id, topic, status, date_completed)
- **Streak:** Consecutive days with at least one completion

## Project structure

```
ai_learning_platform/
├── app.py              # Flask app, auth, routes, DB, streak
├── generator.py        # Recommendation engine (filter by level/skills, build roadmap)
├── database.db         # SQLite DB (created on first run)
├── knowledge_base.json # Topics per domain (AI, Data Science, Web Dev, Cloud)
├── resources.json      # Topic → list of resource URLs
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── signup.html
│   ├── dashboard.html
│   ├── generate.html
│   └── roadmap.html
└── static/
    └── style.css
```

## Installation and run

```bash
cd ai_learning_platform
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

- Sign up → Log in → Dashboard  
- **Generate Path** → fill form → **Generate Roadmap** → view roadmap and mark topics completed  
- Dashboard shows analytics and Chart.js graphs from your progress

## Database

- **users:** id, username, email, password (hashed)  
- **progress:** user_id, topic, status, date_completed  
- **user_roadmap:** user_id, created_at, roadmap_json, domain (last generated roadmap per user for dashboard totals)

## Notes

- No external APIs or model training; recommendation is rule-based from `knowledge_base.json` and `resources.json`.  
- Change `SECRET_KEY` in `app.py` for production.
