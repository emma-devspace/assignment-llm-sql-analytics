"""Generate synthetic test data matching the gaming_mental_health schema.

Use this when the Kaggle CSV is unavailable. Creates a small (10K row) dataset
with the same column structure for development and testing.

Usage:
    python scripts/generate_test_data.py
"""

import random
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "gaming_mental_health.sqlite"
TABLE_NAME = "gaming_mental_health"

COLUMNS = [
    ("respondent_id", "INTEGER"),
    ("age", "INTEGER"),
    ("gender", "TEXT"),
    ("country", "TEXT"),
    ("education_level", "TEXT"),
    ("occupation", "TEXT"),
    ("gaming_platform", "TEXT"),
    ("game_genre", "TEXT"),
    ("hours_per_week", "REAL"),
    ("years_gaming", "INTEGER"),
    ("sessions_per_week", "INTEGER"),
    ("avg_session_duration_hours", "REAL"),
    ("in_game_purchases", "INTEGER"),
    ("gaming_motivation", "TEXT"),
    ("social_interaction_online", "TEXT"),
    ("multiplayer_frequency", "TEXT"),
    ("competitive_play", "INTEGER"),
    ("streaming_content", "INTEGER"),
    ("vr_usage", "INTEGER"),
    ("addiction_level", "REAL"),
    ("anxiety_score", "REAL"),
    ("depression_score", "REAL"),
    ("stress_level", "REAL"),
    ("sleep_quality", "REAL"),
    ("sleep_hours", "REAL"),
    ("physical_activity_hours", "REAL"),
    ("social_support_score", "REAL"),
    ("life_satisfaction", "REAL"),
    ("self_esteem_score", "REAL"),
    ("loneliness_score", "REAL"),
    ("aggression_score", "REAL"),
    ("attention_score", "REAL"),
    ("cognitive_performance", "REAL"),
    ("emotional_regulation", "REAL"),
    ("screen_time_total_hours", "REAL"),
    ("work_life_balance", "REAL"),
    ("relationship_status", "TEXT"),
    ("financial_stress", "REAL"),
    ("bmi", "REAL"),
]

GENDERS = ["Male", "Female", "Non-binary"]
COUNTRIES = ["USA", "UK", "Canada", "Germany", "Japan", "Brazil", "India", "Australia", "South Korea", "France"]
EDUCATION = ["High School", "Bachelor's", "Master's", "PhD", "Some College"]
OCCUPATIONS = ["Student", "Employed", "Self-employed", "Unemployed", "Part-time"]
PLATFORMS = ["PC", "Console", "Mobile", "Multi-platform"]
GENRES = ["FPS", "RPG", "MOBA", "Sports", "Strategy", "Puzzle", "Battle Royale", "Simulation"]
MOTIVATIONS = ["Fun", "Competition", "Social", "Stress Relief", "Achievement", "Escapism"]
SOCIAL_ONLINE = ["High", "Medium", "Low"]
MULTIPLAYER_FREQ = ["Daily", "Weekly", "Rarely", "Never"]
RELATIONSHIPS = ["Single", "In a relationship", "Married", "Divorced"]

NUM_ROWS = 10000


def generate_row(i: int) -> tuple:
    age = random.randint(13, 65)
    gender = random.choice(GENDERS)
    hours_pw = round(random.uniform(1, 60), 1)
    addiction = round(random.uniform(0, 10), 2)
    anxiety = round(random.uniform(0, 10), 2)

    return (
        i,
        age,
        gender,
        random.choice(COUNTRIES),
        random.choice(EDUCATION),
        random.choice(OCCUPATIONS),
        random.choice(PLATFORMS),
        random.choice(GENRES),
        hours_pw,
        random.randint(0, 30),
        random.randint(1, 14),
        round(random.uniform(0.5, 8), 1),
        random.randint(0, 1),
        random.choice(MOTIVATIONS),
        random.choice(SOCIAL_ONLINE),
        random.choice(MULTIPLAYER_FREQ),
        random.randint(0, 1),
        random.randint(0, 1),
        random.randint(0, 1),
        addiction,
        anxiety,
        round(random.uniform(0, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(1, 10), 2),
        round(random.uniform(4, 10), 1),
        round(random.uniform(0, 14), 1),
        round(random.uniform(0, 10), 2),
        round(random.uniform(1, 10), 2),
        round(random.uniform(1, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(0, 10), 2),
        round(random.uniform(2, 16), 1),
        round(random.uniform(1, 10), 2),
        random.choice(RELATIONSHIPS),
        round(random.uniform(0, 10), 2),
        round(random.uniform(16, 40), 1),
    )


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in COLUMNS)
    cursor.execute(f'CREATE TABLE "{TABLE_NAME}" ({col_defs})')

    placeholders = ", ".join(["?"] * len(COLUMNS))
    sql = f'INSERT INTO "{TABLE_NAME}" VALUES ({placeholders})'

    random.seed(42)
    batch = []
    for i in range(1, NUM_ROWS + 1):
        batch.append(generate_row(i))
        if len(batch) >= 1000:
            cursor.executemany(sql, batch)
            batch = []
    if batch:
        cursor.executemany(sql, batch)

    conn.commit()

    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    count = cursor.fetchone()[0]
    print(f"Created {DB_PATH} with {count:,} rows and {len(COLUMNS)} columns")

    cursor.execute(f"PRAGMA table_info({TABLE_NAME})")
    cols = cursor.fetchall()
    print(f"Columns: {[c[1] for c in cols]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
