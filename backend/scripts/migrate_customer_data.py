"""
customer_data 目录结构迁移脚本（一次性）

迁移目标：
  1. customer_data/customer_data/db_knowledge/ → customer_data/superadmin/db_knowledge/
  2. customer_data/customer_data/reports/      → customer_data/superadmin/reports/
  3. customer_data/reports/*.md               → customer_data/superadmin/reports/
  4. customer_data/.claude/skills/user/*.md   → .claude/skills/user/superadmin/
     （同名文件: 保留目标处已有的较新版本，原文件重命名为 .bak）
  5. 清理迁移后留下的空目录

使用方法:
    python backend/scripts/migrate_customer_data.py --dry-run   # 预览
    python backend/scripts/migrate_customer_data.py             # 正式执行
"""
import sys
import shutil
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))


def _move(src: Path, dst: Path, dry_run: bool, mode: str) -> None:
    if dry_run:
        print(f"  [MOVE] {src.relative_to(_ROOT)} → {dst.relative_to(_ROOT)}")
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(f"  Moved: {src.relative_to(_ROOT)} → {dst.relative_to(_ROOT)}")


def _move_conflict(src: Path, dst: Path, dry_run: bool) -> None:
    """同名冲突时: 保留目标（更新版本），原文件改为 .bak 保存。"""
    bak = dst.with_suffix(dst.suffix + ".bak")
    if dry_run:
        print(f"  [CONFLICT] {src.relative_to(_ROOT)}")
        print(f"           目标已存在: {dst.relative_to(_ROOT)} (保留)")
        print(f"           原文件另存: {bak.relative_to(_ROOT)}")
    else:
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(bak))
        src.unlink()
        print(f"  Conflict: kept {dst.relative_to(_ROOT)}, bak: {bak.relative_to(_ROOT)}")


def _rmdir_if_empty(path: Path, dry_run: bool) -> None:
    """递归删除空目录（仅在所有子文件都已迁走后）。"""
    if not path.exists() or not path.is_dir():
        return
    # 先递归清理子目录
    for child in sorted(path.iterdir(), reverse=True):
        if child.is_dir():
            _rmdir_if_empty(child, dry_run)
    # 再检查自身是否为空
    remaining = list(path.iterdir())
    if not remaining:
        if dry_run:
            print(f"  [RMDIR] {path.relative_to(_ROOT)}")
        else:
            path.rmdir()
            print(f"  Removed empty dir: {path.relative_to(_ROOT)}")


def run(dry_run: bool = False) -> None:
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{mode}customer_data 目录结构迁移")
    print(f"{'='*60}\n")

    customer_data = _ROOT / "customer_data"
    nested = customer_data / "customer_data"
    superadmin_dir = customer_data / "superadmin"
    skills_superadmin = _ROOT / ".claude" / "skills" / "user" / "superadmin"

    # ── 步骤 1: customer_data/customer_data/ → customer_data/superadmin/ ──────
    print("步骤 1: 迁移 customer_data/customer_data/ 内容")
    if nested.exists():
        for item in sorted(nested.iterdir()):
            dst = superadmin_dir / item.name
            if dst.exists():
                print(f"  [SKIP] 目标已存在，跳过: {dst.relative_to(_ROOT)}")
            else:
                _move(item, dst, dry_run, mode)
    else:
        print(f"  [SKIP] {nested.relative_to(_ROOT)} 不存在")

    # ── 步骤 2: customer_data/reports/ → customer_data/superadmin/reports/ ────
    print("\n步骤 2: 迁移 customer_data/reports/ 根目录散落文件")
    root_reports = customer_data / "reports"
    if root_reports.exists():
        dst_reports = superadmin_dir / "reports"
        for md in sorted(root_reports.glob("*.md")):
            dst = dst_reports / md.name
            if dst.exists():
                print(f"  [SKIP] 目标已存在: {dst.relative_to(_ROOT)}")
            else:
                _move(md, dst, dry_run, mode)
        # 清理空的 reports/ 目录
        _rmdir_if_empty(root_reports, dry_run)
    else:
        print(f"  [SKIP] {root_reports.relative_to(_ROOT)} 不存在")

    # ── 步骤 3: customer_data/.claude/skills/user/ → .claude/skills/user/superadmin/ ──
    print("\n步骤 3: 归位误放的 skill 文件")
    stray_skills = customer_data / ".claude" / "skills" / "user"
    if stray_skills.exists():
        for md in sorted(stray_skills.glob("*.md")):
            dst = skills_superadmin / md.name
            if dst.exists():
                _move_conflict(md, dst, dry_run)
            else:
                _move(md, dst, dry_run, mode)
        # 清理空目录链
        _rmdir_if_empty(customer_data / ".claude", dry_run)
    else:
        print(f"  [SKIP] {stray_skills.relative_to(_ROOT)} 不存在")

    # ── 步骤 4: 清理空的 customer_data/customer_data/ ─────────────────────────
    print("\n步骤 4: 清理空目录")
    _rmdir_if_empty(nested, dry_run)

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY-RUN 完成。以上为预览，未实际执行任何操作。")
        print("正式执行请去掉 --dry-run 参数。")
    else:
        print("迁移完成。")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="customer_data 目录结构迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
