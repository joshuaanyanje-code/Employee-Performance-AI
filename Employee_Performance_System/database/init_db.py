from db import get_connection

conn = get_connection()
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users(
id INTEGER PRIMARY KEY,
username TEXT,
password TEXT,
role TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS employees(
id INTEGER PRIMARY KEY,
name TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS topics(
id INTEGER PRIMARY KEY,
topic TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ratings(
id INTEGER PRIMARY KEY,
rater TEXT,
employee TEXT,
topic TEXT,
score INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS suggestions(
id INTEGER PRIMARY KEY,
message TEXT
)
""")

conn.commit()
print("Database initialized")
