"""
Seed the verified career master record from the user's resume facts.

Run once to populate the SQLite DB with the master record. This is the
one-time manual data-entry step (human-in-the-loop: the user confirms the
facts are correct).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db.repository import execute_sql, query_one


def seed_master_record():
    """Insert the verified master record. Idempotent (checks for existing row)."""
    existing = query_one("SELECT id FROM career_profile LIMIT 1")
    if existing:
        print("Master record already exists; skipping seed.")
        return

    # career_profile
    execute_sql(
        "INSERT INTO career_profile (full_name, email, phone, location, linked_url, portfolio_url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "MD Nafiz Mahfuz",
            "nafizmahfuz100@gmail.com",
            "+880 1877-014405",
            "Dhaka, Bangladesh",
            None,
            None,
        ),
    )

    # employment
    execute_sql(
        "INSERT INTO employment (company, role, start_date, end_date, current, location) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Betopia Limited", "Business Analyst", "02/2024", None, 1, "Dhaka, Bangladesh"),
    )
    emp_id = query_one("SELECT id FROM employment")["id"]

    # achievements
    achievements = [
        (
            "Reduced cost per unit by 12% using SQL and Power BI dashboards",
            "SQL, Power BI",
            "12%",
        ),
        (
            "Analyzed slow-moving stock worth $50,000+ at Walton Hi-Tech",
            "Excel, SQL",
            "$50,000+",
        ),
        (
            "Built a Random Forest no-show prediction model achieving 82% accuracy",
            "Python, Scikit-learn",
            "82%",
        ),
    ]
    for bullet, tools, metric in achievements:
        execute_sql(
            "INSERT INTO achievement (employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?)",
            (emp_id, bullet, tools, metric),
        )

    # skills
    skills = [
        ("Python", "Advanced"), ("Pandas", "Advanced"), ("NumPy", "Intermediate"),
        ("Scikit-learn", "Intermediate"), ("SQL", "Advanced"),
        ("Power BI", "Intermediate"), ("Excel", "Advanced"), ("VBA", "Intermediate"),
        ("Solver", "Beginner"),
    ]
    for name, level in skills:
        execute_sql(
            "INSERT OR IGNORE INTO skill (name, level) VALUES (?, ?)",
            (name, level),
        )

    # education
    execute_sql(
        "INSERT INTO education (institution, degree, year, verified) VALUES (?, ?, ?, ?)",
        ("BRAC University", "B.Sc. in Computer Science & Engineering", 2024, 1),
    )

    # constraints
    execute_sql(
        "INSERT INTO constraints (target_roles, target_countries, min_salary, notice_period_days, visa_needs) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "Business Analyst, Systems Analyst, Data Analyst",
            "UK, EU, USA, Canada, Australia, New Zealand, Middle East",
            30000,
            30,
            "None — eligible for remote B2B/EOR",
        ),
    )

    print("Master record seeded successfully.")


if __name__ == "__main__":
    seed_master_record()
