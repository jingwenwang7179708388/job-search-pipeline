#!/usr/bin/env python3
"""
recommendation/create_test_user.py - insert the Day 2 test user into users (once)

The user is looked up by name; it is inserted only when no row with that name exists, so
reruns never create a second copy. Prints the user's row either way.

Usage (from the project root): python3 recommendation/create_test_user.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn          # noqa: E402

TEST_USER = {
    "name": "Test user (DS/ML, 1 yr)",
    "target_tracks": ["DATA_SCIENCE", "ML_ENGINEERING", "ML_RESEARCH"],
    "skills": ["python", "sql", "spark", "pytorch"],      # same names as job_features.skills
    "years_experience": 1,
    "preferred_locations": ["CA", "NY", "Remote"],
    "needs_sponsorship": True,
}


def main():
    u = TEST_USER
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE name = %s", (u["name"],))
        row = cur.fetchone()
        if row:
            print(f"Already exists: user_id {row[0]} (nothing inserted)")
        else:
            cur.execute(
                "INSERT INTO users (name, target_tracks, skills, years_experience, "
                "preferred_locations, needs_sponsorship) VALUES (%s, %s, %s, %s, %s, %s)",
                (u["name"], json.dumps(u["target_tracks"]), json.dumps(u["skills"]),
                 u["years_experience"], json.dumps(u["preferred_locations"]),
                 u["needs_sponsorship"]))
            conn.commit()
            print(f"Inserted user_id {cur.lastrowid}")
        cur.execute("SELECT user_id, name, target_tracks, skills, years_experience, "
                    "preferred_locations, needs_sponsorship, created_at FROM users WHERE name = %s",
                    (u["name"],))
        cols = [d[0] for d in cur.description]
        for col, val in zip(cols, cur.fetchone()):
            print(f"  {col:<20} {val}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
