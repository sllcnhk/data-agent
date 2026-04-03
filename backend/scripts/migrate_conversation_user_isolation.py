"""
对话用户隔离 DB 迁移脚本（一次性）

迁移内容：
  1. conversations 表加 user_id UUID 列（可空，FK → users.id ON DELETE SET NULL）
  2. conversation_groups 表加 user_id UUID 列（可空，FK → users.id ON DELETE SET NULL）
  3. 存量数据归属 superadmin（user_id = superadmin 的 UUID）
  4. 建索引

使用方法:
    python backend/scripts/migrate_conversation_user_isolation.py --dry-run
    python backend/scripts/migrate_conversation_user_isolation.py
"""
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))


def run(dry_run: bool = False) -> None:
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{mode}对话用户隔离 DB 迁移")
    print(f"{'='*60}\n")

    from sqlalchemy import text
    from backend.config.database import SessionLocal

    db = SessionLocal()
    try:
        # ── 步骤 1: 检查列是否已存在 ──────────────────────────────────────────
        def column_exists(table: str, column: str) -> bool:
            result = db.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).scalar()
            return result > 0

        # ── 步骤 2: conversations 表加 user_id 列 ────────────────────────────
        print("步骤 1: conversations 表加 user_id 列")
        if column_exists("conversations", "user_id"):
            print("  [SKIP] 列已存在，跳过")
        else:
            sql = """
                ALTER TABLE conversations
                ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL
            """
            if dry_run:
                print(f"  [SQL] {sql.strip()}")
            else:
                db.execute(text(sql))
                db.commit()
                print("  [OK] 已添加 conversations.user_id 列")

        # ── 步骤 3: conversation_groups 表加 user_id 列 ─────────────────────
        print("\n步骤 2: conversation_groups 表加 user_id 列")
        if column_exists("conversation_groups", "user_id"):
            print("  [SKIP] 列已存在，跳过")
        else:
            sql = """
                ALTER TABLE conversation_groups
                ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL
            """
            if dry_run:
                print(f"  [SQL] {sql.strip()}")
            else:
                db.execute(text(sql))
                db.commit()
                print("  [OK] 已添加 conversation_groups.user_id 列")

        # ── 步骤 4: 获取 superadmin 的 UUID ──────────────────────────────────
        print("\n步骤 3: 将存量数据归属 superadmin")
        superadmin = db.execute(text(
            "SELECT id FROM users WHERE username = 'superadmin' LIMIT 1"
        )).fetchone()

        if not superadmin:
            print("  [WARN] 未找到 superadmin 用户，跳过存量数据迁移")
        else:
            superadmin_id = superadmin[0]
            print(f"  superadmin UUID: {superadmin_id}")

            sql_conv = "UPDATE conversations SET user_id = :uid WHERE user_id IS NULL"
            sql_grp = "UPDATE conversation_groups SET user_id = :uid WHERE user_id IS NULL"
            if dry_run:
                print(f"  [SQL] {sql_conv} -- uid={superadmin_id}")
                print(f"  [SQL] {sql_grp} -- uid={superadmin_id}")
            else:
                conv_count = db.execute(text(
                    "SELECT COUNT(*) FROM conversations WHERE user_id IS NULL"
                )).scalar()
                db.execute(text(sql_conv), {"uid": superadmin_id})
                db.commit()
                print(f"  [OK] 已将 {conv_count} 条对话归属 superadmin")

                grp_count = db.execute(text(
                    "SELECT COUNT(*) FROM conversation_groups WHERE user_id IS NULL"
                )).scalar()
                db.execute(text(sql_grp), {"uid": superadmin_id})
                db.commit()
                print(f"  [OK] 已将 {grp_count} 个分组归属 superadmin")

        # ── 步骤 5: 建索引 ───────────────────────────────────────────────────
        print("\n步骤 4: 建索引")

        def index_exists(name: str) -> bool:
            result = db.execute(text(
                "SELECT COUNT(*) FROM pg_indexes WHERE indexname = :n"
            ), {"n": name}).scalar()
            return result > 0

        indexes = [
            ("idx_conversations_user_id",
             "CREATE INDEX idx_conversations_user_id ON conversations(user_id)"),
            ("idx_conversation_groups_user_id",
             "CREATE INDEX idx_conversation_groups_user_id ON conversation_groups(user_id)"),
        ]
        for idx_name, idx_sql in indexes:
            if index_exists(idx_name):
                print(f"  [SKIP] 索引 {idx_name} 已存在")
            elif dry_run:
                print(f"  [SQL] {idx_sql}")
            else:
                db.execute(text(idx_sql))
                db.commit()
                print(f"  [OK] 已建索引 {idx_name}")

    except Exception as exc:
        db.rollback()
        print(f"\n[ERROR] 迁移失败: {exc}")
        raise
    finally:
        db.close()

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY-RUN 完成。以上为预览，未实际执行任何操作。")
        print("正式执行请去掉 --dry-run 参数。")
    else:
        print("迁移完成。")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="对话用户隔离 DB 迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
