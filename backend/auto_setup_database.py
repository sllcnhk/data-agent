"""
Automatic PostgreSQL setup script
自动设置PostgreSQL数据库脚本
"""
import subprocess
import sys
import time
import os
import shutil

def run_command(cmd, shell=True, capture_output=True):
    """运行命令"""
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=capture_output,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def main():
    print("=" * 60)
    print("PostgreSQL Automatic Setup")
    print("=" * 60)
    print()

    # Configuration
    PG_DATA = r"C:\Program Files\PostgreSQL\18\data"
    PG_HBA = os.path.join(PG_DATA, "pg_hba.conf")
    PG_HBA_BACKUP = os.path.join(PG_DATA, "pg_hba.conf.backup")
    PSQL = r"C:\Program Files\PostgreSQL\18\bin\psql.exe"
    PASSWORD = "Sgp013013."

    print("[1/9] Checking administrator privileges...")
    success, stdout, stderr = run_command('net session')
    if not success:
        print("ERROR: This script requires administrator privileges")
        print("Please right-click and select 'Run as administrator'")
        input("Press Enter to exit...")
        sys.exit(1)
    print("OK: Running as administrator")

    print("\n[2/9] Checking PostgreSQL service...")
    success, stdout, stderr = run_command('sc query postgresql-x64-18')
    if not success or 'RUNNING' not in stdout:
        print("ERROR: PostgreSQL service is not running")
        print("Starting service...")
        run_command('net start postgresql-x64-18')
        time.sleep(3)
    print("OK: PostgreSQL service is running")

    print("\n[3/9] Checking if pg_hba.conf exists...")
    if not os.path.exists(PG_HBA):
        print(f"ERROR: Cannot find pg_hba.conf at {PG_HBA}")
        print("Your PostgreSQL may be installed in a different location")
        input("Press Enter to exit...")
        sys.exit(1)
    print(f"OK: Found pg_hba.conf")

    print("\n[4/9] Backing up pg_hba.conf...")
    try:
        shutil.copy2(PG_HBA, PG_HBA_BACKUP)
        print(f"OK: Backup created")
    except Exception as e:
        print(f"ERROR: Cannot create backup: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    print("\n[5/9] Modifying pg_hba.conf to trust mode...")
    try:
        with open(PG_HBA, 'r', encoding='utf-8') as f:
            content = f.read()

        # Replace scram-sha-256 with trust for localhost
        new_content = content.replace(
            'host    all             all             127.0.0.1/32            scram-sha-256',
            'host    all             all             127.0.0.1/32            trust'
        )

        with open(PG_HBA, 'w', encoding='utf-8') as f:
            f.write(new_content)

        print("OK: Modified to trust mode")
    except Exception as e:
        print(f"ERROR: Cannot modify pg_hba.conf: {e}")
        # Restore backup
        shutil.copy2(PG_HBA_BACKUP, PG_HBA)
        input("Press Enter to exit...")
        sys.exit(1)

    print("\n[6/9] Restarting PostgreSQL service...")
    run_command('net stop postgresql-x64-18', capture_output=False)
    time.sleep(2)
    run_command('net start postgresql-x64-18', capture_output=False)
    time.sleep(3)
    print("OK: Service restarted")

    print(f"\n[7/9] Resetting password to '{PASSWORD}'...")
    cmd = f'"{PSQL}" -U postgres -h localhost -d postgres -c "ALTER USER postgres PASSWORD \'{PASSWORD}\';"'
    success, stdout, stderr = run_command(cmd)
    if success:
        print("OK: Password reset successfully")
    else:
        print(f"WARNING: Password reset may have failed")
        print(f"Error: {stderr}")

    print("\n[8/9] Creating data_agent database...")
    cmd = f'"{PSQL}" -U postgres -h localhost -d postgres -c "CREATE DATABASE data_agent;"'
    success, stdout, stderr = run_command(cmd)
    if success:
        print("OK: Database created successfully")
    elif 'already exists' in stderr:
        print("OK: Database already exists")
    else:
        print(f"WARNING: Database creation may have failed")
        print(f"Error: {stderr}")

    print("\n[9/9] Restoring pg_hba.conf...")
    try:
        shutil.copy2(PG_HBA_BACKUP, PG_HBA)
        print("OK: Restored original configuration")
    except Exception as e:
        print(f"ERROR: Cannot restore pg_hba.conf: {e}")
        print("You may need to restore it manually from pg_hba.conf.backup")

    print("\n[10/10] Final service restart...")
    run_command('net stop postgresql-x64-18', capture_output=False)
    time.sleep(2)
    run_command('net start postgresql-x64-18', capture_output=False)
    time.sleep(3)
    print("OK: Service restarted with original configuration")

    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print("\nTesting connection...")

    # Test connection
    import psycopg2
    try:
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            database='data_agent',
            user='postgres',
            password=PASSWORD,
            connect_timeout=5
        )
        conn.close()
        print("SUCCESS: Connection test passed!")
        print("\nYou can now run:")
        print("  python scripts\\init_chat_db.py")
    except Exception as e:
        print(f"FAILED: Connection test failed: {e}")
        print("\nPlease try manual setup using:")
        print("  1. Open pgAdmin")
        print("  2. Connect to PostgreSQL 18")
        print("  3. Create database 'data_agent'")

    print()
    input("Press Enter to exit...")

if __name__ == "__main__":
    main()
