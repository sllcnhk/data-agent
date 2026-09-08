"""
合并 CSV 文件服务（小工具）

职责分层：
    csv_merge_core.py   纯逻辑：奇偶扫描、表头定位、编码分类、排序、校验、拼接
    本模块              job 状态机、DB 落库与节流、协作式取消、磁盘预检、
                        export_jobs 反查、行数对账

线程模型：
    run_merge_job 是 async 包装器，把同步阻塞的字节拷贝交给线程池。
    线程池 **max_workers=1** —— 字节拼接是顺序磁盘 IO，两个 job 并跑吞吐直接砍半，
    而且提交时的磁盘空间预检会失效（两个 job 各自以为空间够）。
    因此同一时刻只有一个 job 在跑，其余排在 pending。
"""
import asyncio
import fnmatch
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.services.csv_merge_core import (
    DEFAULT_CHUNK_SIZE,
    FileProbe,
    MergeResult,
    ValidationResult,
    merge_csv_files,
    probe_file,
    sort_files,
    validate_batch,
)

logger = logging.getLogger(__name__)

# 串行：见模块 docstring
_MERGE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="merge-csv-worker")

#: DB 进度写入节流：每 N 字节或每 M 秒（取先到者）写一次
PROGRESS_BYTES_INTERVAL = 64 * 1024 * 1024
PROGRESS_SECONDS_INTERVAL = 2.0
#: 取消状态查询节流：core 每 chunk 都会问，这里按时间节流，避免打爆 DB
CANCEL_POLL_SECONDS = 1.0
#: 磁盘空间安全系数
DISK_SAFETY_FACTOR = 1.1
#: provenance 未知且 mtime 在此秒数内 → 给 warning（**不阻断**，需求方明确要求）
RECENT_MTIME_WARN_SECONDS = 60
#: 预估耗时用的吞吐（MB/s）。实测奇偶扫描 235 MB/s，拷贝与之同一遍完成，
#: 取 150 作为保守估计（含磁盘写入与其他进程竞争）。
ESTIMATE_THROUGHPUT_MB_S = 150

_TERMINAL_EXPORT_STATUSES = {"completed", "failed", "cancelled"}


# ─────────────────────────────────────────────────────────────────────────────
# Job 存取辅助
# ─────────────────────────────────────────────────────────────────────────────

def _is_cancelling(job_id: str) -> bool:
    from backend.config.database import SessionLocal
    from backend.models.merge_csv_job import MergeCsvJob
    db = SessionLocal()
    try:
        j = db.query(MergeCsvJob).filter(MergeCsvJob.id == job_id).first()
        return j is not None and j.status == "cancelling"
    finally:
        db.close()


def _update_job(job_id: str, **fields) -> None:
    from backend.config.database import SessionLocal
    from backend.models.merge_csv_job import MergeCsvJob
    db = SessionLocal()
    try:
        j = db.query(MergeCsvJob).filter(MergeCsvJob.id == job_id).first()
        if j is None:
            return
        for k, v in fields.items():
            setattr(j, k, v)
        j.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _mark_running(job_id: str) -> Optional[Dict[str, Any]]:
    """pending → running，并处理启动竞态（cancelling → 直接 cancelled）。

    返回 Job 快照；None 表示已终止或不存在，调用方应停止。
    """
    from backend.config.database import SessionLocal
    from backend.models.merge_csv_job import MergeCsvJob
    db = SessionLocal()
    try:
        job = db.query(MergeCsvJob).filter(MergeCsvJob.id == job_id).first()
        if not job:
            logger.error("[MergeCsvJob %s] Job not found, aborting.", job_id)
            return None
        if job.status == "cancelling":
            job.status = "cancelled"
            job.finished_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()
            db.commit()
            logger.info("[MergeCsvJob %s] Cancelled before start.", job_id)
            return None
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        return {
            "username": job.username,
            "job_name": job.job_name,
            "has_header": bool(job.has_header),
            "strict_header": bool(job.strict_header),
            "sort_mode": job.sort_mode or "natural",
            "source_files": list(job.source_files or []),
        }
    finally:
        db.close()


def _mark_failed(job_id: str, msg: str, **extra) -> None:
    _update_job(job_id, status="failed", finished_at=datetime.utcnow(), error_message=msg, **extra)


# ─────────────────────────────────────────────────────────────────────────────
# 路径与目录
# ─────────────────────────────────────────────────────────────────────────────

def customer_data_root() -> Path:
    from backend.config.settings import settings
    return (
        Path(settings.allowed_directories[0])
        if settings.allowed_directories
        else Path("customer_data")
    )


def user_root(username: str) -> Path:
    return customer_data_root() / username


def output_dir(username: str) -> Path:
    return user_root(username) / "tools" / "merge_csv" / "jobs"


def upload_dir(username: str) -> Path:
    return user_root(username) / "tools" / "merge_csv" / "uploads"


def resolve_user_path(username: str, raw: str) -> Path:
    """把外部传入的路径限制在 customer_data/{username}/ 内。

    `..` 穿越、绝对路径指向别的用户目录、符号链接跳出去 —— 全部在这里拦掉。
    resolve() 之后再做 is_relative_to 判定，不能只做字符串前缀比较。
    """
    root = user_root(username).resolve()
    p = Path(raw).expanduser()
    p = (root / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError(f"路径超出允许范围（仅限 customer_data/{username}/）：{raw}")
    return p


def list_csv_dirs(username: str) -> List[Dict[str, Any]]:
    """列出用户目录下所有含 .csv 的目录（入口②）。"""
    root = user_root(username)
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted({p.parent for p in root.rglob("*.csv")}):
        csvs = [p for p in d.glob("*.csv") if p.is_file()]
        if not csvs:
            continue
        latest = max(p.stat().st_mtime for p in csvs)
        out.append({
            "dir_path": str(d),
            "display_path": str(d.relative_to(root)) if d != root else ".",
            "csv_files": len(csvs),
            "total_size": sum(p.stat().st_size for p in csvs),
            "latest_mtime": datetime.fromtimestamp(latest).isoformat(),
        })
    out.sort(key=lambda x: x["latest_mtime"], reverse=True)
    return out


def list_csv_files(username: str, dir_path: str, pattern: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出指定目录下的 .csv（入口③）。**只列 .csv**，.zip 等一律忽略。"""
    d = resolve_user_path(username, dir_path)
    if not d.is_dir():
        raise FileNotFoundError(f"目录不存在：{dir_path}")
    files = []
    for p in sorted(d.glob("*.csv")):
        if not p.is_file():
            continue
        if pattern and not fnmatch.fnmatch(p.name, pattern):
            continue
        files.append({
            "filename": p.name,
            "file_path": str(p),
            "size": p.stat().st_size,
            "origin": "server",
        })
    return files


# ─────────────────────────────────────────────────────────────────────────────
# export_jobs 反查（入口① + V7 防护 + 对账数据源）
# ─────────────────────────────────────────────────────────────────────────────

def _iter_export_job_files(job) -> List[Dict[str, Any]]:
    """从一个 export_job 里取出所有 CSV 产物（单文件 + date_chunked 分块）。

    字段是 `output_files`（不是 chunk_files）。实测形状（date_chunked）：
        {index, date_start, date_end, filename, file_path, file_size,
         rows, sheets, status, _depth, _retry_count}
    其中 `rows` 是导出侧自报的**数据行数**（不含表头）—— 实测 132102 对上本工具
    扫出的 132103 条记录减去表头，两条独立计数路径精确一致，可直接用于对账。

    注意 date_chunked 模式下 `job.file_path` 指向最终打包的 `.zip`，不是 CSV，
    所以单文件回退分支不会误命中。
    """
    out: List[Dict[str, Any]] = []
    for ch in (job.output_files or []):
        fp = ch.get("file_path")
        if fp and str(fp).lower().endswith(".csv"):
            out.append({
                "filename": ch.get("filename") or Path(fp).name,
                "file_path": fp,
                "size": ch.get("file_size") or 0,
                "origin": "server",
                "export_job_id": str(job.id),
                "expected_rows": ch.get("rows"),
                "chunk_status": ch.get("status"),
            })
    if not out and job.file_path and str(job.file_path).lower().endswith(".csv"):
        out.append({
            "filename": job.output_filename or Path(job.file_path).name,
            "file_path": job.file_path,
            "size": job.file_size or 0,
            "origin": "server",
            "export_job_id": str(job.id),
            "expected_rows": getattr(job, "total_rows", None),
            "chunk_status": job.status,
        })
    return out


def list_export_jobs(db, username: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """入口①：列出本用户产出过 CSV 的导出任务及其分块。

    未完成的分块**自动排除但要让用户看见**（incomplete_chunks）；
    CSV 已被压缩、原文件不在了的分块单列出来（compressed_chunks），
    因为那种情况用户需要先解压，而不是以为文件丢了。
    """
    from backend.models.export_job import ExportJob

    q = (
        db.query(ExportJob)
        .filter(ExportJob.username == username)
        .order_by(ExportJob.created_at.desc())
    )
    total = q.count()
    jobs = q.offset((page - 1) * page_size).limit(page_size).all()

    items: List[Dict[str, Any]] = []
    for job in jobs:
        all_files = _iter_export_job_files(job)
        if not all_files:
            continue

        usable, incomplete, compressed = [], [], []
        for f in all_files:
            if f.get("chunk_status") and f["chunk_status"] not in ("completed", None):
                incomplete.append(f["filename"])
                continue
            p = Path(f["file_path"])
            if not p.exists():
                if p.with_suffix(".zip").exists():
                    compressed.append(f["filename"])
                else:
                    incomplete.append(f["filename"])
                continue
            f["size"] = p.stat().st_size          # 用磁盘真实大小，不信 DB 里的旧值
            usable.append(f)

        if not usable and not incomplete and not compressed:
            continue

        rows = [f.get("expected_rows") for f in usable]
        items.append({
            "export_job_id": str(job.id),
            "job_name": getattr(job, "job_name", None) or job.output_filename,
            "status": job.status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "csv_files": len(usable),
            "total_size": sum(f["size"] for f in usable),
            "total_rows": sum(r for r in rows if r) if all(r is not None for r in rows) and rows else None,
            "incomplete_chunks": incomplete,
            "compressed_chunks": compressed,
            "files": [{k: v for k, v in f.items() if k != "chunk_status"} for f in usable],
        })

    return {"total": total, "items": items}


def _build_active_export_path_index(db, username: str) -> Dict[str, str]:
    """把「仍在进行中的导出任务」写入的所有 CSV 路径建成索引（V7 主防线）。

    这是最权威的「文件是否写完」判据 —— 比 mtime 启发式可靠得多，而且对已完成
    的导出零误伤（状态就是 completed，不会命中）。
    """
    from backend.models.export_job import ExportJob

    index: Dict[str, str] = {}
    active = (
        db.query(ExportJob)
        .filter(ExportJob.username == username)
        .filter(~ExportJob.status.in_(_TERMINAL_EXPORT_STATUSES))
        .all()
    )
    for job in active:
        # 显式再判一次终态。不只依赖上面的 SQL 过滤 —— 这个不变量（"只有未完成的
        # 导出才算活跃"）关系到会不会把已完成的导出误判成"仍在写入"而阻断合并，
        # 值得在 Python 侧也钉一遍，且成本为零。
        if job.status in _TERMINAL_EXPORT_STATUSES:
            continue
        for f in _iter_export_job_files(job):
            try:
                index[str(Path(f["file_path"]).resolve())] = job.status
            except OSError:
                index[f["file_path"]] = job.status
    return index


def _build_expected_rows_index(db, username: str) -> Dict[str, Optional[int]]:
    """path → 导出侧自报行数，用于合并完成后的双向对账。"""
    from backend.models.export_job import ExportJob

    index: Dict[str, Optional[int]] = {}
    for job in db.query(ExportJob).filter(ExportJob.username == username).all():
        for f in _iter_export_job_files(job):
            if f.get("expected_rows") is None:
                continue
            try:
                index[str(Path(f["file_path"]).resolve())] = f["expected_rows"]
            except OSError:
                pass
    return index


# ─────────────────────────────────────────────────────────────────────────────
# 提交前预检（V1–V10）
# ─────────────────────────────────────────────────────────────────────────────

def run_preview(
    db,
    username: str,
    source_files: List[Dict[str, Any]],
    *,
    has_header: bool = True,
    strict_header: bool = True,
    sort_mode: str = "natural",
    allow_active_files: bool = False,
) -> Dict[str, Any]:
    """跑完 V1–V10 全部校验，返回结构化结果。**不建 job。**"""
    if not source_files:
        return {
            "ok": False, "errors": ["未提供任何待合并文件"], "warnings": [],
            "sorted_files": [], "baseline_header": [], "col_count": 0,
            "output_encoding": "", "output_bom": False, "total_bytes": 0,
            "disk_free": None, "disk_required": None,
            "estimated_seconds": None, "expected_total_rows": None,
        }

    ordered = sort_files(source_files, sort_mode)

    # ── V1–V6：逐文件探测 + 批量校验（纯逻辑，在 core 里）──
    probes: List[FileProbe] = [
        probe_file(f["file_path"], f.get("filename"), f.get("origin", "server"))
        for f in ordered
    ]
    v: ValidationResult = validate_batch(
        probes, has_header=has_header, strict_header=strict_header
    )
    errors = list(v.errors)
    warnings = list(v.warnings)

    # 把探测结果回填到清单（锚定 size / 编码 / 表头边界）
    for f, pr in zip(ordered, probes):
        f["size"] = pr.size
        f["encoding"] = pr.encoding.name if pr.encoding else None
        f["bom_len"] = pr.encoding.bom_len if pr.encoding else 0
        f["header_end"] = pr.header.header_end if pr.header else 0

    # ── V7：export_jobs 反查（仅 server 来源）──
    active_index = _build_active_export_path_index(db, username)
    active_hits = []
    for f in ordered:
        if f.get("origin") != "server":
            continue
        try:
            key = str(Path(f["file_path"]).resolve())
        except OSError:
            key = f["file_path"]
        if key in active_index:
            active_hits.append(f"{f['filename']}（导出任务状态 {active_index[key]}）")
    if active_hits:
        errors.append(
            "以下文件对应的导出任务仍在进行中，文件可能还没写完，"
            "合并会把半条记录拼进结果：" + "；".join(active_hits)
        )

    # ── V8：provenance 未知 + 刚被修改 → warning（不阻断）──
    now = time.time()
    suspicious = [
        f["filename"]
        for f, pr in zip(ordered, probes)
        if f.get("origin") == "server"
        and _safe_resolve(f["file_path"]) not in active_index
        and _not_in_any_export(db, username, f["file_path"])
        and (now - pr.mtime) < RECENT_MTIME_WARN_SECONDS
    ]
    if suspicious and not allow_active_files:
        warnings.append(
            f"以下文件无法从导出任务记录中确认来源，且在 {RECENT_MTIME_WARN_SECONDS} 秒内"
            f"刚被修改过 —— 若它仍在被写入，合并结果的末尾可能残缺："
            + "；".join(suspicious)
        )

    # ── V9：磁盘空间预检 ──
    total_bytes = sum(f.get("size") or 0 for f in ordered)
    required = int(total_bytes * DISK_SAFETY_FACTOR)
    disk_free: Optional[int] = None
    try:
        od = output_dir(username)
        od.mkdir(parents=True, exist_ok=True)
        disk_free = shutil.disk_usage(str(od)).free
        if disk_free < required:
            errors.append(
                f"磁盘空间不足：需要约 {_gb(required)}（源文件 {_gb(total_bytes)} × "
                f"{DISK_SAFETY_FACTOR}），剩余仅 {_gb(disk_free)}"
            )
    except OSError as e:
        warnings.append(f"无法检查磁盘剩余空间（{e}），请自行确认")

    # ── 对账基线 ──
    rows_index = _build_expected_rows_index(db, username)
    expected: List[Optional[int]] = []
    for f in ordered:
        er = f.get("expected_rows")
        if er is None:
            er = rows_index.get(_safe_resolve(f["file_path"]))
        f["expected_rows"] = er
        expected.append(er)
    expected_total = (
        sum(e for e in expected if e is not None)
        if expected and all(e is not None for e in expected)
        else None
    )

    return {
        "ok": bool(v.ok and not errors),
        "errors": errors,
        "warnings": warnings,
        "sorted_files": ordered,
        "baseline_header": v.baseline_header,
        "col_count": v.col_count,
        "output_encoding": v.output_encoding,
        "output_bom": v.output_bom,
        "total_bytes": total_bytes,
        "disk_free": disk_free,
        "disk_required": required,
        "estimated_seconds": round(total_bytes / (ESTIMATE_THROUGHPUT_MB_S * 1024 * 1024), 1),
        "expected_total_rows": expected_total,
    }


def _safe_resolve(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except OSError:
        return p


def _not_in_any_export(db, username: str, file_path: str) -> bool:
    """该路径是否**完全**不在任何导出任务记录里（provenance 未知）。"""
    idx = _export_path_cache(db, username)
    return _safe_resolve(file_path) not in idx


_EXPORT_PATH_CACHE: Dict[str, Tuple[float, set]] = {}


def _export_path_cache(db, username: str) -> set:
    """所有导出任务产出过的 CSV 路径集合。缓存 10 秒 —— 一次预检里会被问几十次。"""
    from backend.models.export_job import ExportJob

    hit = _EXPORT_PATH_CACHE.get(username)
    if hit and (time.time() - hit[0]) < 10:
        return hit[1]
    paths = set()
    for job in db.query(ExportJob).filter(ExportJob.username == username).all():
        for f in _iter_export_job_files(job):
            paths.add(_safe_resolve(f["file_path"]))
    _EXPORT_PATH_CACHE[username] = (time.time(), paths)
    return paths


def _gb(n: int) -> str:
    if n < 1024 ** 3:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


# ─────────────────────────────────────────────────────────────────────────────
# 合并主流程
# ─────────────────────────────────────────────────────────────────────────────

def _run_merge_sync(job_id: str) -> None:
    snap = _mark_running(job_id)
    if snap is None:
        return

    username: str = snap["username"]
    has_header: bool = snap["has_header"]
    strict_header: bool = snap["strict_header"]
    sort_mode: str = snap["sort_mode"]
    job_name: Optional[str] = snap["job_name"]
    source_files: List[Dict[str, Any]] = snap["source_files"]

    from backend.config.database import SessionLocal

    # ── 重新跑一遍预检 ──
    # 提交与开始执行之间可能隔了很久（串行排队），期间文件可能被改、磁盘可能被占满。
    # 这里重新校验并**重新锚定 size**，比信任提交时的快照安全。
    db = SessionLocal()
    try:
        pv = run_preview(
            db, username, source_files,
            has_header=has_header, strict_header=strict_header, sort_mode=sort_mode,
            allow_active_files=True,   # V8 的 warning 在提交时已给过，这里不重复
        )
    finally:
        db.close()

    if not pv["ok"]:
        _mark_failed(job_id, "开始执行前的复检未通过：" + "；".join(pv["errors"]))
        return

    ordered = pv["sorted_files"]
    total_bytes = pv["total_bytes"]
    warnings: List[str] = list(pv["warnings"])

    _update_job(
        job_id,
        source_files=ordered,
        total_files=len(ordered),
        total_bytes=total_bytes,
        warnings=warnings or None,
    )

    # ── 输出路径 ──
    od = output_dir(username)
    od.mkdir(parents=True, exist_ok=True)
    safe_name = (job_name or "merged").strip() or "merged"
    safe_name = "".join(c for c in safe_name if c not in '\\/:*?"<>|').strip() or "merged"
    output_filename = f"{safe_name}_{job_id}.csv"
    output_path = od / output_filename

    # ── 进度 / 取消回调（节流在这一层，core 每 chunk 都会调用）──
    state = {"last_bytes": 0, "last_ts": 0.0, "cancel_ts": 0.0, "cancelling": False}

    def on_progress(done_bytes: int, done_files: int, rows: int, physical: int) -> None:
        now = time.time()
        if (done_bytes - state["last_bytes"] < PROGRESS_BYTES_INTERVAL
                and now - state["last_ts"] < PROGRESS_SECONDS_INTERVAL):
            return
        state["last_bytes"] = done_bytes
        state["last_ts"] = now
        _update_job(
            job_id,
            done_bytes=done_bytes,
            done_files=done_files,
            total_rows=rows,
            total_physical_lines=physical,
        )

    def on_file_done(idx: int, entry: Dict[str, Any]) -> None:
        _update_job(job_id, last_merged_file=entry.get("filename"), done_files=idx + 1)

    def should_cancel() -> bool:
        now = time.time()
        if now - state["cancel_ts"] < CANCEL_POLL_SECONDS:
            return state["cancelling"]
        state["cancel_ts"] = now
        state["cancelling"] = _is_cancelling(job_id)
        return state["cancelling"]

    # ── 拼接 ──
    try:
        result: MergeResult = merge_csv_files(
            ordered,
            str(output_path),
            has_header=has_header,
            output_bom=bool(pv["output_bom"]),
            chunk_size=DEFAULT_CHUNK_SIZE,
            on_progress=on_progress,
            on_file_done=on_file_done,
            should_cancel=should_cancel,
        )
    except Exception as exc:
        logger.exception("[MergeCsvJob %s] Merge crashed.", job_id)
        _cleanup(output_path)
        _mark_failed(job_id, f"合并失败：{exc}")
        return

    warnings.extend(result.warnings)

    # ── 对账 ──
    reconcile_status, reconcile_detail, expected_total = _reconcile(result, pv)

    common = dict(
        source_files=result.per_file or ordered,
        done_files=result.done_files,
        done_bytes=result.done_bytes,
        total_rows=result.total_rows,
        total_physical_lines=result.total_physical_lines,
        last_merged_file=result.last_merged_file,
        warnings=warnings or None,
        expected_total_rows=expected_total,
        reconcile_status=reconcile_status,
        reconcile_detail=reconcile_detail or None,
        finished_at=datetime.utcnow(),
    )

    if result.status == "failed":
        # 失败：半截文件没价值又可能占几十 GB → 删掉
        _cleanup(output_path)
        _mark_failed(job_id, result.error or "合并失败（未知原因）", **common)
        logger.error("[MergeCsvJob %s] Failed: %s", job_id, result.error)
        return

    # 完成 / 取消都**保留**结果文件（取消时截断在完整文件边界，是有效 CSV）
    _update_job(
        job_id,
        status=result.status,
        output_filename=output_filename,
        file_path=str(output_path),
        file_size=result.output_size,
        output_encoding=("utf-8-sig" if pv["output_bom"] else pv["output_encoding"]),
        **common,
    )
    logger.info(
        "[MergeCsvJob %s] %s: %d rows, %d/%d files, %s, reconcile=%s",
        job_id, result.status, result.total_rows, result.done_files, len(ordered),
        _gb(result.output_size), reconcile_status,
    )


def _reconcile(
    result: MergeResult, pv: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], Optional[int]]:
    """双向行数对账：导出侧自报行数 vs 合并实际统计的行数。

    两个数字来自完全独立的计数路径（ClickHouse 导出侧 vs 本工具的 RFC4180 扫描），
    一致才说明数据没漏 —— 这比任何自证的进度条都有说服力。
    只对**已完整合入**的文件对账；取消掉的尾部文件不参与，否则必然"不一致"。
    """
    detail: List[Dict[str, Any]] = []
    expected_sum = 0
    have_all = bool(result.per_file)

    for e in result.per_file:
        exp = e.get("expected_rows")
        actual = int(e.get("rows") or 0)
        if exp is None:
            have_all = False
        else:
            expected_sum += int(exp)
        detail.append({
            "filename": e.get("filename"),
            "expected_rows": exp,
            "actual_rows": actual,
            "diff": (actual - int(exp)) if exp is not None else 0,
        })

    if not have_all:
        return "unavailable", detail, None
    status = "matched" if expected_sum == result.total_rows else "mismatched"
    return status, detail, expected_sum


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning("[MergeCsv] Failed to delete %s: %s", path, e)


async def run_merge_job(job_id: str) -> None:
    """后台协程包装器：把同步阻塞的合并工作提交到（串行的）线程池。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_MERGE_EXECUTOR, _run_merge_sync, job_id)
