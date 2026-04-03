"""
对话 last_message_at 字段修复脚本（一次性）

问题：历史对话的 last_message_at 字段为 NULL，导致：
  1. 排序时这些对话被推到最后（或丢失）
  2. 前端无法正确显示对话时间

修复策略：
  1. 尝试从 messages 表中获取最新 assistant 消息的 created_at
  2. 如果无消息，回退到 conversation.updated_at
  3. 批量更新所有 NULL 记录

使用方法:
    python backend/scripts/fix_conversation_last_message_at.py --dry-run
    python backend/scripts/fix_conversation_last_message_at.py
"""
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))


def run(dry_run: bool = False) -> None:
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{mode}对话 last_message_at 字段修复")
    print(f"{'='*60}\n")

    from sqlalchemy import text, desc
    from backend.config.database import SessionLocal

    db = SessionLocal()
    try:
        # ── 步骤 1: 检查需要修复的对话数量 ───────────────────────────
        print("步骤 1: 检查需要修复的对话数量")
        count_null = db.execute(text(
            "SELECT COUNT(*) FROM conversations WHERE last_message_at IS NULL"
        )).scalar()
        print(f"  发现 {count_null} 条 last_message_at 为 NULL 的对话")

        if count_null == 0:
            print("  [SKIP] 无需修复")
            return

        # ── 步骤 2: 基于 messages 表更新 ─────────────────────────────
        print("\n步骤 2: 基于 messages 表更新 last_message_at")

        # 先查询几条示例数据看看
        sample_rows = db.execute(text("""
            SELECT c.id, c.title, c.updated_at, c.last_message_at,
                   (SELECT MAX(created_at) FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'assistant') as latest_msg_at
            FROM conversations c
            WHERE c.last_message_at IS NULL
            LIMIT 5
        """)).fetchall()

        print(f"  示例数据（前 5 条）:")
        for row in sample_rows:
            print(f"    - {row.title[:40]}: latest_msg_at={row.latest_msg_at}, updated_at={row.updated_at}")

        # 执行更新
        sql_update_from_messages = """
            UPDATE conversations c
            SET last_message_at = (
                SELECT MAX(m.created_at)
                FROM messages m
                WHERE m.conversation_id = c.id AND m.role = 'assistant'
            )
            WHERE c.last_message_at IS NULL
              AND EXISTS (
                SELECT 1 FROM messages m2
                WHERE m2.conversation_id = c.id AND m2.role = 'assistant'
              )
        """

        if dry_run:
            print(f"  [SQL] {sql_update_from_messages.strip()[:200]}...")
        else:
            result = db.execute(text(sql_update_from_messages))
            updated_count = result.rowcount
            db.commit()
            print(f"  [OK] 已基于 messages 表更新 {updated_count} 条对话")

        # ── 步骤 3: 剩余无消息的对话，回退到 updated_at ────────────────────
        print("\n步骤 3: 剩余无消息的对话，回退到 updated_at")

        # 检查剩余 NULL 的数量
        remaining_null = db.execute(text(
            "SELECT COUNT(*) FROM conversations WHERE last_message_at IS NULL"
        )).scalar()

        if remaining_null > 0:
            print(f"  发现 {remaining_null} 条无消息的对话，回退到 updated_at")

            sql_update_from_updated_at = """
                UPDATE conversations
                SET last_message_at = updated_at
                WHERE last_message_at IS NULL
            """

            if dry_run:
                print(f"  [SQL] {sql_update_from_updated_at.strip()}")
            else:
                result = db.execute(text(sql_update_from_updated_at))
                updated_count = result.rowcount
                db.commit()
                print(f"  [OK] 已基于 updated_at 更新 {updated_count} 条对话")
        else:
            print("  [SKIP] 所有对话已修复，无需回退")

        # ── 步骤 4: 验证修复结果 ─────────────────────────────────────────
        print("\n步骤 4: 验证修复结果")

        final_null_count = db.execute(text(
            "SELECT COUNT(*) FROM conversations WHERE last_message_at IS NULL"
        )).scalar()

        if final_null_count == 0:
            print("  [OK] ✅ 所有对话的 last_message_at 均已修复")
        else:
            print(f"  [WARN] 仍有 {final_null_count} 条对话为 NULL（可能数据异常）")

        # 显示修复后的示例数据
        sample_fixed = db.execute(text("""
            SELECT title, last_message_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            LIMIT 3
        """)).fetchall()

        print(f"\n  修复后的示例数据（最新 3 条）:")
        for row in sample_fixed:
            print(f"    - {row.title[:40]}: last_message_at={row.last_message_at}")

    except Exception as exc:
        db.rollback()
        print(f"\n[ERROR] 修复失败: {exc}")
        raise
    finally:
        db.close()

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY-RUN 完成。以上为预览，未实际执行任何操作。")
        print("正式执行请去掉 --dry-run 参数。")
    else:
        print("修复完成。")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="对话 last_message_at 字段修复")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
