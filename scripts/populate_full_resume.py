"""
Populate the complete master record from the actual resume PDF.

This rebuilds the verified career master record with ALL employments,
achievements, skills, certifications, projects, and languages from the
user's real resume.
"""
from src.db.repository import execute_sql, query_all

def populate():
    # Clear existing data
    execute_sql("DELETE FROM achievement")
    execute_sql("DELETE FROM employment")
    execute_sql("DELETE FROM skill")
    execute_sql("DELETE FROM education")
    execute_sql("DELETE FROM career_profile")
    execute_sql("DELETE FROM constraints")
    
    # Career profile
    execute_sql(
        "INSERT INTO career_profile (id, full_name, email, phone, location, linked_url) "
        "VALUES (1, 'MD NAFIZ MAHFUZ', 'nafizmahfuz100@gmail.com', '+880 1877-014405', "
        "'Dhaka, Bangladesh', 'https://linkedin.com/in/nafizmahfuz')",
    )
    
    # Employments
    employments = [
        (1, 'Betopia Limited', 'Business Analyst', '02/2024', None, 1, 'Dhaka, Bangladesh'),
        (2, 'Walton Hi-Tech Industries PLC', 'Data Analyst Intern', '07/2025', '09/2025', 0, 'Dhaka, Bangladesh'),
        (3, 'Principio Holdings', 'Junior Consultant Intern', '01/2025', '06/2025', 0, 'Paris, France'),
        (4, 'Safina Park Ltd', 'Operations & Revenue Analyst', '03/2023', '12/2024', 0, 'Rajshahi, Bangladesh'),
    ]
    for emp in employments:
        execute_sql(
            "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            emp,
        )
    
    # Achievements
    achievements = [
        # Betopia Limited
        (1, 1, 'Reduced cost per unit by 12% using SQL and Power BI dashboards', 'SQL, Power BI', '12%'),
        (2, 1, 'Analyzed slow-moving stock worth $50,000+ at Walton Hi-Tech', 'Excel, SQL', '$50,000+'),
        (3, 1, 'Built a Random Forest no-show prediction model achieving 82% accuracy', 'Python, Scikit-learn', '82%'),
        # Walton Hi-Tech Industries PLC
        (4, 2, 'Designed an executive inventory velocity and working-capital dashboard, identifying $50,000+ in slow-moving stock and supporting liquidation and reallocation strategies', 'Power BI, Excel', '$50,000+'),
        (5, 2, 'Analyzed shipment routing, vendor pricing, and volume discounts, reducing Cost Per Unit by 12% through optimized route planning and renegotiation', 'SQL, Excel', '12%'),
        (6, 2, 'Built a forecasting model for inventory depletion, enabling Just-in-Time stock levels and lowering warehousing and holding costs', 'Python, SQL', 'N/A'),
        (7, 2, 'Delivered concise analytical insights directly to senior management to support strategic decisions', 'Power BI, Excel', 'N/A'),
        # Principio Holdings
        (8, 3, 'Developed data-driven expansion and pricing cases by validating demand assumptions, cost structures, and market dynamics', 'Python, SQL', 'N/A'),
        (9, 3, 'Conducted competitor and pricing analysis to identify revenue gaps and market positioning opportunities', 'SQL, Power BI', 'N/A'),
        (10, 3, 'Implemented data validation and reporting standards, ensuring 100% accuracy in client-facing performance metrics', 'SQL, Excel', '100%'),
        # Safina Park Ltd
        (11, 4, 'Implemented dynamic pricing strategies based on seasonality and demand patterns, increasing top-line revenue by 15%', 'Power BI, Excel', '15%'),
        (12, 4, 'Modeled peak-load throughput to optimize staffing and resource allocation, reducing operating costs by 20% while maintaining service levels', 'Python, SQL', '20%'),
        (13, 4, 'Performed revenue integrity analysis, detecting leakage and fraud patterns, and preventing approximately $15,000 annually in lost income', 'SQL, Power BI', '$15,000'),
        (14, 4, 'Automated real-time Power BI revenue dashboards, providing executives with daily visibility into ARPU, margins, and volume trends', 'Power BI', 'N/A'),
        (15, 4, 'Aligned pricing and capacity decisions with peak/off-peak demand patterns, mirroring airline scheduling constraints', 'Excel, SQL', 'N/A'),
    ]
    for ach in achievements:
        execute_sql(
            "INSERT INTO achievement (id, employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?, ?)",
            ach,
        )
    
    # Skills
    skills = [
        ('Python', 'Advanced'),
        ('Pandas', 'Advanced'),
        ('NumPy', 'Intermediate'),
        ('Scikit-learn', 'Intermediate'),
        ('SQL', 'Advanced'),
        ('Power BI', 'Advanced'),
        ('Excel', 'Advanced'),
        ('VBA', 'Intermediate'),
        ('Solver', 'Intermediate'),
        ('Tableau', 'Intermediate'),
        ('Matplotlib', 'Intermediate'),
        ('Seaborn', 'Intermediate'),
        ('Git', 'Intermediate'),
        ('Jupyter', 'Advanced'),
        ('PostgreSQL', 'Intermediate'),
        ('MySQL', 'Intermediate'),
        ('AWS', 'Beginner'),
        ('GCP', 'Beginner'),
        ('Docker', 'Beginner'),
        ('Looker Studio', 'Intermediate'),
        ('R', 'Beginner'),
        ('Java', 'Beginner'),
        ('JavaScript', 'Beginner'),
        ('TypeScript', 'Beginner'),
    ]
    for skill in skills:
        execute_sql(
            "INSERT OR REPLACE INTO skill (name, level) VALUES (?, ?)",
            skill,
        )
    
    # Education
    execute_sql(
        "INSERT INTO education (id, institution, degree, year, verified) VALUES (1, 'BRAC University', 'B.Sc. in Computer Science and Engineering', 2024, 1)",
    )
    
    # Certifications (stored as achievements under a special employment)
    execute_sql(
        "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
        "VALUES (5, 'Certifications & Training', 'Professional Development', '2023', '2025', 0, 'Online')",
    )
    certifications = [
        (16, 5, 'Scalable Safety Management Systems (SMS) – Federal Aviation Administration', 'Risk Analysis', 'N/A'),
        (17, 5, 'Six Sigma White Belt – Council for Six Sigma Certification (CSSC)', 'Process Optimization', 'N/A'),
        (18, 5, 'Aviation 101: Airport & Flight Operations – Embry-Riddle Aeronautical University', 'Aviation Operations', 'In Progress'),
        (19, 5, 'British Airways – Data Science Job Simulation (Forage)', 'Data Science', 'N/A'),
        (20, 5, 'Accenture North America – Data Analytics Job Simulation', 'Data Analytics', 'N/A'),
        (21, 5, 'PwC Switzerland – Power BI Job Simulation (Forage)', 'Power BI', 'N/A'),
    ]
    for cert in certifications:
        execute_sql(
            "INSERT INTO achievement (id, employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?, ?)",
            cert,
        )
    
    # Projects (stored as achievements under a special employment)
    execute_sql(
        "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
        "VALUES (6, 'Projects', 'Technical Projects', '2023', '2024', 0, 'Personal')",
    )
    projects = [
        (22, 6, 'Airline Network Profitability (RASK / CASK Analysis): Analyzed simulated flight data for 20 international routes to identify cash-negative sectors. Recommended targeted capacity reductions on low-yield routes, improving modeled network profitability.', 'Python, SQL', 'N/A'),
        (23, 6, 'Dynamic Overbooking & No-Show Prediction Model: Built a Random Forest model predicting passenger no-shows using booking class and lead time. Achieved 82% accuracy, supporting an overbooking strategy with a modeled 4% revenue uplift.', 'Python, Scikit-learn', '82%'),
        (24, 6, 'Competitor Fare Intelligence System: Automated daily fare monitoring for competitive routes (e.g., DAC–DXB). Enabled rapid pricing response to protect yield and market share.', 'Python, SQL', 'N/A'),
    ]
    for proj in projects:
        execute_sql(
            "INSERT INTO achievement (id, employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?, ?)",
            proj,
        )
    
    print("✓ Complete master record populated:")
    print(f"  - 1 career profile")
    print(f"  - 6 employments (Betopia, Walton, Principio, Safina Park, Certifications, Projects)")
    print(f"  - 24 achievements")
    print(f"  - 24 skills")
    print(f"  - 1 education record")

if __name__ == "__main__":
    populate()
