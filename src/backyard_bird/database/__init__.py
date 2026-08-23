"""SQLite access — the authoritative detection record (§11).

connection.py opens the database with the required pragmas.
migrations.py applies migrations/*.sql. repositories.py holds every
parameterized query; nothing outside this package should write raw SQL.
"""
