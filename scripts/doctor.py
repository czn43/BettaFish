#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""BettaFish 启动自检 / Doctor.

用法（项目根目录）:
    .\\.venv\\Scripts\\python.exe scripts\\doctor.py

只做只读检查：包版本、关键 import、端口占用、.env 配置、MySQL 连通性。
任何一项 FAIL 都会在结尾的 SUMMARY 里列出来，并给出对应的修法提示。
"""

from __future__ import annotations

import os
import socket
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []
WARNS: list[str] = []


def head(title: str) -> None:
    print()
    print("=" * 68)
    print(title)
    print("=" * 68)


def ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def warn(msg: str) -> None:
    WARNS.append(msg)
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  [FAIL] {msg}")


def last_line(exc: BaseException) -> str:
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    lines = [ln.strip() for ln in "".join(tb).splitlines() if ln.strip()]
    return lines[-1] if lines else repr(exc)


def check_python() -> None:
    head("1. Python")
    print(f"  executable : {sys.executable}")
    print(f"  version    : {sys.version.split()[0]}")
    print(f"  cwd        : {os.getcwd()}")
    print(f"  utf8 mode  : {sys.flags.utf8_mode}")
    if sys.version_info < (3, 9):
        fail(f"Python {sys.version.split()[0]} 太旧，需要 >=3.9")
    in_venv = (ROOT / '.venv').resolve() in Path(sys.executable).resolve().parents
    if in_venv:
        ok("运行在项目 .venv 中")
    else:
        warn("当前解释器不是项目 .venv，依赖可能不全")


def check_packages() -> None:
    head("2. 关键依赖版本")
    probes = [
        ('numpy', '2.0'),
        ('pyarrow', '14'),
        ('pandas', '2.0'),
        ('streamlit', '1.28'),
        ('flask', '2.3'),
        ('flask_socketio', '5'),
        ('torch', '2.0'),
        ('transformers', '4.30'),
        ('sentence_transformers', '2.2'),
        ('sklearn', '1.3'),
        ('pymysql', '1.1'),
        ('sqlalchemy', '2.0'),
        ('openai', '1.3'),
        ('loguru', '0.7'),
        ('dotenv', '1.0'),
    ]
    for name, minver in probes:
        try:
            mod = __import__(name)
            ver = getattr(mod, '__version__', '?')
            ok(f"{name:<22} {ver}")
        except Exception as exc:
            fail(f"{name:<22} 导入失败: {last_line(exc)}  (需要 >={minver})")

    # numpy 2 与 streamlit 1.28 的元数据声明冲突，但实际 import 可用；这里只做提示
    try:
        import numpy
        major = int(numpy.__version__.split('.')[0])
        if major >= 2:
            print()
            print("  note: streamlit 1.28 的 metadata 写的是 numpy<2，但 import 实测可用；")
            print("        若 streamlit 运行时仍报 numpy 相关错误，改用 pyarrow==18.1.0 + numpy 1.26.4。")
    except Exception:
        pass


def check_imports() -> None:
    head("3. 项目模块 import")
    # app.py 顶层的导入链，任何一条挂了都会让 `python app.py` 直接退出
    targets = [
        ("config.settings", "from config import settings"),
        ("MindSpider.main", "from MindSpider.main import MindSpider"),
        ("ReportEngine.flask_interface", "from ReportEngine.flask_interface import report_bp, initialize_report_engine"),
        ("ForumEngine.monitor", "from ForumEngine.monitor import start_forum_monitoring"),
    ]
    for label, stmt in targets:
        try:
            exec(stmt, {})
            ok(label)
        except Exception as exc:
            if label.startswith('ReportEngine'):
                warn(f"{label} 导入失败（可降级）: {last_line(exc)}")
            else:
                fail(f"{label} 导入失败: {last_line(exc)}")

    # 三个 Streamlit 子应用真正的重依赖
    heavy = [
        ("sentence_transformers", "streamlit run 时 InsightEngine 需要"),
        ("sklearn.cluster", "InsightEngine 聚类需要"),
        ("matplotlib", "ReportEngine 公式渲染需要"),
    ]
    for label, why in heavy:
        try:
            __import__(label)
            ok(label)
        except Exception as exc:
            fail(f"{label} 导入失败（{why}）: {last_line(exc)}")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def check_ports() -> None:
    head("4. 端口占用（占用的端口会让 app.py 启动即崩）")
    for port, who in [(5000, 'Flask 主应用'), (8501, 'insight'), (8502, 'media'), (8503, 'query')]:
        if port_free(port):
            ok(f"{port} 空闲  ({who})")
        else:
            fail(f"{port} 被占用  ({who}) → 停掉占用进程: Get-NetTCPConnection -LocalPort {port} | "
                 f"Select OwningProcess  (或 taskkill /PID <pid> /F)")


def read_env() -> dict:
    env_path = ROOT / '.env'
    data: dict[str, str] = {}
    if not env_path.exists():
        return data
    for raw in env_path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        data[k.strip()] = v.strip()
    return data


def check_env() -> None:
    head("5. .env 配置")
    env = read_env()
    if not env:
        fail(".env 不存在或为空")
        return
    ok(f".env 已读取，共 {len(env)} 项")
    for key in ('HOST', 'PORT', 'DB_DIALECT', 'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_NAME'):
        print(f"  {key:<22} {env.get(key, '<missing>')}")
    print()
    print("  API keys（空值只影响对应功能，不阻止系统启动）:")
    for key in ('INSIGHT_ENGINE_API_KEY', 'MEDIA_ENGINE_API_KEY', 'QUERY_ENGINE_API_KEY',
                'REPORT_ENGINE_API_KEY', 'FORUM_HOST_API_KEY', 'KEYWORD_OPTIMIZER_API_KEY'):
        val = env.get(key, '')
        mark = 'set' if val else 'EMPTY'
        print(f"  {key:<30} {mark}")


def check_mysql(env: dict) -> None:
    head("6. 数据库连通性")
    if env.get('DB_DIALECT', '').lower() not in ('mysql', ''):
        print(f"  DB_DIALECT={env.get('DB_DIALECT')}，跳过 MySQL 检查")
        return
    try:
        import pymysql
    except Exception as exc:
        fail(f"pymysql 不可用: {last_line(exc)}")
        return
    host = env.get('DB_HOST', '127.0.0.1')
    port = int(env.get('DB_PORT', '3306') or 3306)
    user = env.get('DB_USER', 'root')
    pwd = env.get('DB_PASSWORD', '')
    name = env.get('DB_NAME', 'bettafish')
    try:
        conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                               database=name, charset='utf8mb4', connect_timeout=5)
    except Exception as exc:
        fail(f"连接 {user}@{host}:{port}/{name} 失败: {last_line(exc)}")
        return
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION()')
            print(f"  server version : {cur.fetchone()[0]}")
            cur.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s', (name,))
            print(f"  tables in {name} : {cur.fetchone()[0]}")
        ok(f"数据库 {name} 可连接")
    finally:
        conn.close()


def main() -> int:
    print("BettaFish doctor — read-only environment check")
    check_python()
    check_packages()
    check_imports()
    check_ports()
    env = read_env()
    check_env()
    check_mysql(env)

    head("SUMMARY")
    if not FAILS and not WARNS:
        print("  全部通过。可以启动: .\\.venv\\Scripts\\python.exe app.py")
        return 0
    for i, msg in enumerate(FAILS, 1):
        print(f"  FAIL {i}. {msg}")
    for i, msg in enumerate(WARNS, 1):
        print(f"  WARN {i}. {msg}")
    print()
    print("  把上面整段输出贴回对话即可。")
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(main())
