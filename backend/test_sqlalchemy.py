"""
Test SQLAlchemy connection
"""
from sqlalchemy import create_engine, text

# Method 1: Direct connection string
print("=" * 60)
print("Test 1: Direct connection string")
print("=" * 60)
try:
    url = "postgresql://postgres:Sgp013013@localhost:5432/data_agent"
    engine = create_engine(url, echo=False)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version();"))
        version = result.fetchone()[0]
        print("SUCCESS: Connected via SQLAlchemy")
        print("Version:", version[:60])
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")

# Method 2: With settings
print("\n" + "=" * 60)
print("Test 2: Using settings.get_database_url()")
print("=" * 60)
try:
    from config.settings import settings
    url = settings.get_database_url()
    print(f"URL: {url[:30]}...{url[-20:]}")
    print(f"Password in URL: '{settings.postgres_password}'")

    engine = create_engine(url, echo=False)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version();"))
        version = result.fetchone()[0]
        print("SUCCESS: Connected via settings")
        print("Version:", version[:60])
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# Method 3: psycopg2 direct
print("\n" + "=" * 60)
print("Test 3: Direct psycopg2 (baseline)")
print("=" * 60)
try:
    import psycopg2
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        database='data_agent',
        user='postgres',
        password='Sgp013013'
    )
    cursor = conn.cursor()
    cursor.execute('SELECT version();')
    version = cursor.fetchone()[0]
    print("SUCCESS: Connected via psycopg2")
    print("Version:", version[:60])
    cursor.close()
    conn.close()
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
