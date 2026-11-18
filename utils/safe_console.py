#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""控制台输出兜底。

背景
----
各引擎（QueryEngine / MediaEngine / InsightEngine / ReportEngine / MindSpider）
里散落着大量 `print(...)`。这些代码在 Streamlit 子进程里跑时，stdout 是被
`app.py` 用 `subprocess.PIPE` 接管的管道：

    app.py  --PIPE-->  streamlit 子进程 stdout  --read_process_output-->  logs/<app>.log

一旦父进程（app.py）先死掉，子进程就变成孤儿，管道写端还在、读端没了。此后
任何 `print` 都会抛：

    OSError: [Errno 22] Invalid argument

这个异常会从业务代码里直接冒出来，把整条分析链路打断 —— 但它跟业务毫无关系，
纯粹是"日志没人接"。

另一类问题是编码：Windows 控制台默认 GBK，print 中文/emoji 会抛
UnicodeEncodeError: 'gbk' codec can't encode character ...

做法
----
install_safe_print() 把 builtins.print 换成一个永不抛异常的版本：
  1. 正常写 stdout；
  2. 写失败（OSError / UnicodeEncodeError / ValueError）→ 降级写 stderr；
  3. 再失败 → 直接丢弃。

业务逻辑不该因为"日志写不出去"而崩掉。
"""

from __future__ import annotations

import builtins
import io
import sys

_INSTALLED = False
_ORIGINAL_PRINT = builtins.print


def _sanitize(value) -> str:
    """把任意对象转成一定能编码的字符串（emoji/生僻字降级为 ?）。"""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return '<unprintable>'
    return text.encode('utf-8', 'replace').decode('utf-8', 'replace')


def _safe_print(*args, **kwargs):
    """永不抛异常的 print。"""
    # 1) 正常路径
    try:
        return _ORIGINAL_PRINT(*args, **kwargs)
    except (OSError, UnicodeEncodeError, ValueError):
        pass
    except Exception:
        pass

    # 2) 降级：清洗文本后写 stderr
    try:
        cleaned = tuple(_sanitize(a) for a in args)
        fallback_kwargs = {k: v for k, v in kwargs.items() if k not in ('file', 'flush')}
        return _ORIGINAL_PRINT(*cleaned, file=sys.stderr, flush=False, **fallback_kwargs)
    except Exception:
        pass

    # 3) 放弃
    return None


def _reconfigure_streams() -> None:
    """尽力把 stdout/stderr 切到 utf-8 + replace，减少乱码和编码异常。"""
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def install_safe_print(reconfigure: bool = True) -> None:
    """幂等地安装安全 print。"""
    global _INSTALLED
    if _INSTALLED:
        return
    if reconfigure:
        _reconfigure_streams()
    builtins.print = _safe_print
    _INSTALLED = True


def safe_print(*args, **kwargs):
    """可直接调用的版本（不想改 builtins 时用）。"""
    return _safe_print(*args, **kwargs)


def ensure_stream_writable() -> bool:
    """探测 stdout 是否还能写；孤儿进程场景下会返回 False。"""
    try:
        sys.stdout.write('')
        sys.stdout.flush()
        return True
    except Exception:
        return False


if __name__ == '__main__':
    install_safe_print()
    print('safe print installed')
    print('emoji test:', '\U0001f3d3', '\u4e2d\u6587\u6d4b\u8bd5')
