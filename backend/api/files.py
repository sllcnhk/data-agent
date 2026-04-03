"""
文件下载 API

提供对 customer_data/{username}/ 下文件的安全下载接口。
"""
import logging
import mimetypes
import os
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from backend.api.deps import get_current_user
from backend.config.settings import settings

router = APIRouter(prefix="/files", tags=["files"])

# 项目根目录（settings.py 所在目录向上三级）
_PROJECT_ROOT: Path = Path(settings.__class__.__module__
                           .replace(".", "/")).parent.parent.parent.resolve()
# 更可靠的方式：直接从 allowed_directories 推导根目录
# allowed_directories[0] 形如 /xxx/data-agent/customer_data
_CUSTOMER_DATA_ROOT: Path = Path(
    settings.allowed_directories[0]
) if settings.allowed_directories else Path("customer_data")


def _resolve_download_path(file_path: str, username: str) -> Path:
    """
    解析并验证下载路径安全性。

    规则：
    - 路径必须在 customer_data/{username}/ 下
    - 禁止目录穿越（.. 等）

    Returns:
        解���后的绝对路径

    Raises:
        HTTPException 403/404
    """
    logger = logging.getLogger(__name__)

    # 统一斜杠
    normalized = file_path.replace("\\", "/")

    # 诊断日志：记录输入
    logger.debug(f"[files] 下载请求: user={username}, input_path={file_path}, normalized={normalized}")

    # 尝试拼接 customer_data_root + 相对路径（文件路径可能已含 customer_data/前缀）
    # 兼容两种格式：
    #   1. "customer_data/alice/2026-03/report.csv"  (含根目录前缀)
    #   2. "alice/2026-03/report.csv"                (不含根目录前缀)
    customer_data_name = _CUSTOMER_DATA_ROOT.name  # e.g. "customer_data"

    if normalized.startswith(customer_data_name + "/"):
        # 格式1：去掉根目录前缀
        rel = normalized[len(customer_data_name) + 1:]
    else:
        rel = normalized

    abs_path = (_CUSTOMER_DATA_ROOT / rel).resolve()

    # 诊断日志：记录解析结果
    logger.debug(f"[files] 解析后: rel={rel}, abs_path={abs_path}")

    # 安全边界：必须在 customer_data/{username}/ 下
    user_root = (_CUSTOMER_DATA_ROOT / username).resolve()
    try:
        abs_path.relative_to(user_root)
    except ValueError:
        logger.warning(
            f"[files] 路径验证失败(403): user={username}, abs_path={abs_path}, user_root={user_root}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该文件",
        )

    if not abs_path.exists():
        logger.warning(f"[files] 文件不存在(404): abs_path={abs_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在",
        )

    if not abs_path.is_file():
        logger.warning(f"[files] 路径不是文件(400): abs_path={abs_path}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="路径不是文件",
        )

    logger.info(f"[files] 下载路径验证成功: {abs_path.name}")
    return abs_path


@router.get("/download")
async def download_file(
    path: str = Query(..., description="文件相对路径，如 customer_data/alice/report.csv"),
    current_user=Depends(get_current_user),
):
    """
    下载 customer_data/{username}/ 下的文件。

    - 路径在 customer_data/{username}/ 以外 → 403
    - 文件不存在 → 404
    - 成功 → 触发浏览器下载
    """
    username = getattr(current_user, "username", "default")
    abs_path = _resolve_download_path(path, username)

    filename = abs_path.name
    # URL 编码文件名，支持中文及特殊字符
    encoded_name = urllib.parse.quote(filename)

    # 推断 MIME 类型
    mime_type, _ = mimetypes.guess_type(str(abs_path))
    if not mime_type:
        mime_type = "application/octet-stream"

    return FileResponse(
        path=str(abs_path),
        media_type=mime_type,
        filename=filename,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{encoded_name}"
            )
        },
    )
