@echo off
chcp 65001 >nul
echo ============================================================
echo PostgreSQL Docker 快速安装脚本 (Windows)
echo ============================================================
echo.

REM 检查 Docker
where docker >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] Docker 未安装
    echo.
    echo 请先安装 Docker Desktop for Windows:
    echo https://www.docker.com/products/docker-desktop
    echo.
    pause
    exit /b 1
)

echo [1/3] 检查 Docker 服务...
docker ps >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] Docker 未运行
    echo 请启动 Docker Desktop 后重试
    pause
    exit /b 1
)

echo Docker 正常运行
echo.

echo [2/3] 启动 PostgreSQL 容器...
docker ps -a | find "data-agent-postgres" >nul
if %errorLevel% equ 0 (
    echo 检测到已存在的容器，正在删除...
    docker rm -f data-agent-postgres
)

echo 创建并启动 PostgreSQL 容器...
docker run -d ^
    --name data-agent-postgres ^
    -e POSTGRES_PASSWORD=postgres ^
    -e POSTGRES_DB=data_agent ^
    -p 5432:5432 ^
    -v data-agent-pgdata:/var/lib/postgresql/data ^
    postgres:14

if %errorLevel% neq 0 (
    echo [错误] PostgreSQL 容器启动失败
    pause
    exit /b 1
)

echo PostgreSQL 容器启动成功
echo 等待数据库初始化...
timeout /t 5 /nobreak >nul

echo.
echo [3/3] 安装 Python PostgreSQL 驱动...
cd /d "%~dp0backend"
pip install psycopg2-binary

if %errorLevel% neq 0 (
    echo [警告] psycopg2-binary 安装失败，尝试 psycopg2...
    pip install psycopg2
)

echo.
echo ============================================================
echo PostgreSQL (Docker) 安装完成！
echo ============================================================
echo.
echo 容器信息:
echo   容器名: data-agent-postgres
echo   主机: localhost
echo   端口: 5432
echo   数据库: data_agent
echo   用户: postgres
echo   密码: postgres
echo.
echo Docker 命令:
echo   查看状态: docker ps
echo   查看日志: docker logs data-agent-postgres
echo   停止容器: docker stop data-agent-postgres
echo   启动容器: docker start data-agent-postgres
echo   删除容器: docker rm -f data-agent-postgres
echo.
echo 下一步:
echo   1. 运行: cd backend
echo   2. 运行: python scripts\init_chat_db.py
echo   3. 运行: python main.py
echo.
pause
