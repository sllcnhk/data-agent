"""
PDF 导出服务

使用 Playwright (headless Chromium) 将 HTML 报告渲染为 PDF。

依赖安装：
  pip install playwright
  playwright install chromium

优先级：
  1. Playwright（质量最高，支持 ECharts 渲染）
  2. 降级：weasyprint（纯 Python，无需 Chromium，但图表质量较低）
  3. 最后降级：直接复制 HTML（不生成 PDF，用于测试环境）
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


async def html_to_pdf(html_path: str, output_path: str, wait_ms: int = 2000) -> str:
    """
    将本地 HTML 文件渲染为 PDF。

    Args:
        html_path:   HTML 文件绝对路径
        output_path: PDF 输出绝对路径
        wait_ms:     等待 ECharts 动画完成的毫秒数

    Returns:
        output_path

    Raises:
        RuntimeError: 所有渲染方式均失败时抛出
    """
    html_path = str(Path(html_path).resolve())

    # 方案 1: Playwright
    try:
        return await _playwright_pdf(html_path, output_path, wait_ms)
    except ImportError:
        logger.warning("[PDF] playwright 未安装，尝试 weasyprint 降级")
    except Exception as e:
        logger.warning("[PDF] playwright 失败: %s，尝试 weasyprint 降级", e)

    # 方案 2: weasyprint
    try:
        return _weasyprint_pdf(html_path, output_path)
    except ImportError:
        logger.warning("[PDF] weasyprint 未安装，使用 HTML 复制兜底")
    except Exception as e:
        logger.warning("[PDF] weasyprint 失败: %s", e)

    # 方案 3: 兜底（仅用于测试）
    logger.error("[PDF] 无可用 PDF 渲染器，以 .html 兜底保存")
    fallback = output_path.replace(".pdf", "_fallback.html")
    import shutil
    shutil.copy2(html_path, fallback)
    return fallback


async def _playwright_pdf(html_path: str, output_path: str, wait_ms: int) -> str:
    """使用 Playwright 无头 Chromium 渲染 PDF。"""
    from playwright.async_api import async_playwright

    file_url = Path(html_path).as_uri()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = await browser.new_page()

        # 加载 HTML（通过 file:// URL）
        await page.goto(file_url, wait_until="networkidle", timeout=30000)

        # 等待 ECharts 动画完成
        await page.wait_for_timeout(wait_ms)

        # 导出 PDF（A4 横向）
        await page.pdf(
            path=output_path,
            format="A4",
            landscape=True,
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
        )
        await browser.close()

    logger.info("[PDF] Playwright 导出完成: %s", output_path)
    return output_path


def _weasyprint_pdf(html_path: str, output_path: str) -> str:
    """使用 weasyprint 将 HTML 转 PDF（备选方案）。"""
    import weasyprint

    file_url = Path(html_path).as_uri()
    html = weasyprint.HTML(url=file_url)
    html.write_pdf(output_path)
    logger.info("[PDF] weasyprint 导出完成: %s", output_path)
    return output_path
