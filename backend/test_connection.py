"""
PostgreSQL connection diagnostic tool
"""
import psycopg2
from psycopg2 import OperationalError
import sys

def test_connection():
    """Test PostgreSQL connection with detailed error reporting"""

    configs = [
        {
            'name': 'Default postgres database',
            'host': 'localhost',
            'port': 5432,
            'database': 'postgres',
            'user': 'postgres',
            'password': 'Sgp013013.'
        },
        {
            'name': 'data_agent database',
            'host': 'localhost',
            'port': 5432,
            'database': 'data_agent',
            'user': 'postgres',
            'password': 'Sgp013013.'
        }
    ]

    print("=" * 60)
    print("PostgreSQL Connection Test")
    print("=" * 60)

    for config in configs:
        print(f"\nTesting: {config['name']}")
        print(f"  Host: {config['host']}")
        print(f"  Port: {config['port']}")
        print(f"  Database: {config['database']}")
        print(f"  User: {config['user']}")

        try:
            conn = psycopg2.connect(
                host=config['host'],
                port=config['port'],
                database=config['database'],
                user=config['user'],
                password=config['password'],
                connect_timeout=5
            )

            # Get PostgreSQL version
            cursor = conn.cursor()
            cursor.execute('SELECT version();')
            version = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            print(f"  Result: SUCCESS")
            print(f"  Version: {version[:60]}...")

        except OperationalError as e:
            error_msg = str(e).strip()
            print(f"  Result: FAILED")
            print(f"  Error: {error_msg}")

            # Detailed error analysis
            if 'password authentication failed' in error_msg.lower():
                print("  Issue: Incorrect password")
                print("  Solution: Verify password or reset using:")
                print("    ALTER USER postgres PASSWORD 'new_password';")
            elif 'database' in error_msg.lower() and 'does not exist' in error_msg.lower():
                print("  Issue: Database does not exist")
                print("  Solution: Create database using:")
                print("    CREATE DATABASE data_agent;")
            elif 'could not connect to server' in error_msg.lower():
                print("  Issue: Server not accessible")
                print("  Solution: Check if PostgreSQL service is running")
            else:
                print("  Issue: Unknown error")

        except Exception as e:
            print(f"  Result: ERROR")
            print(f"  Error: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    test_connection()
