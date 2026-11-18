#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子进程生命周期守护。

要解决的两个实际问题
--------------------
1. **孤儿进程**：`app.py` 用 `subprocess.PIPE` 接管 Streamlit 子进程的 stdout。
   如果 `app.py` 被直接关窗口 / 任务管理器结束 / 崩溃，子进程不会跟着死，它变成
   孤儿继续占着 8501/8502/8503。此时它的 stdout 管道读端已经没了，业务代码里任何
   `print(...)` 都会抛 `OSError: [Errno 22] Invalid argument`，整条分析链路被打断。
   而浏览器打开 8503 看到的还是这个"僵尸"页面，于是报错看起来毫无道理。

2. **端口被占**：上一轮留下的孤儿还在监听，新一轮 `streamlit run` 直接
   `Port 8503 is already in use` 退出，前端只看到"启动失败"。

做法
----
- Windows 上用 **Job Object + JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE**：把子进程挂进
  一个作业对象，父进程一死（哪怕是被强杀），句柄关闭，系统连带杀掉所有子进程。
- 启动子应用前先检查端口，若被"我们自己启动的 streamlit"占着就清掉。
- 非 Windows 平台退化为 `prctl(PR_SET_PDEATHSIG)` 不可用时什么都不做（Linux 上
  app.py 退出时子进程本来也会收到 SIGHUP）。
"""

from __future__ import annotations

import os
import subprocess
import sys

_JOB_HANDLE = None
_JOB_READY = False

# --------------------------------------------------------------------------
# Windows Job Object
# --------------------------------------------------------------------------

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


def _init_job_object():
    """创建 kill-on-close 作业对象；失败则静默降级。"""
    global _JOB_HANDLE, _JOB_READY
    if _JOB_READY:
        return _JOB_HANDLE
    _JOB_READY = True

    if sys.platform != 'win32':
        return None

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_ulonglong),
                ('WriteOperationCount', ctypes.c_ulonglong),
                ('OtherOperationCount', ctypes.c_ulonglong),
                ('ReadTransferCount', ctypes.c_ulonglong),
                ('WriteTransferCount', ctypes.c_ulonglong),
                ('OtherTransferCount', ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        ok = kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle),
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
            return None

        _JOB_HANDLE = handle
        return handle
    except Exception:
        return None


def assign_to_job(process) -> bool:
    """把子进程挂进 kill-on-close 作业对象。返回是否成功。"""
    handle = _init_job_object()
    if not handle or process is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        return bool(
            kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(handle), wintypes.HANDLE(int(process._handle))
            )
        )
    except Exception:
        return False


# --------------------------------------------------------------------------
# 端口占用排查
# --------------------------------------------------------------------------

def _netstat_pids(port: int) -> list[int]:
    """返回监听指定端口的 PID 列表（Windows 用 netstat，POSIX 用 lsof）。"""
    pids: list[int] = []
    try:
        if sys.platform == 'win32':
            out = subprocess.run(
                ['netstat', '-ano', '-p', 'TCP'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            ).stdout
            needle = f':{port}'
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == 'TCP' and parts[3] == 'LISTENING':
                    if parts[1].endswith(needle):
                        try:
                            pids.append(int(parts[4]))
                        except ValueError:
                            pass
        else:
            out = subprocess.run(
                ['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for tok in out.split():
                try:
                    pids.append(int(tok))
                except ValueError:
                    pass
    except Exception:
        pass
    return sorted(set(pids))


def _pid_is_our_streamlit(pid: int) -> bool:
    """判断 PID 是不是本项目启动的 streamlit 进程（避免误杀别人的服务）。"""
    try:
        if sys.platform == 'win32':
            out = subprocess.run(
                ['wmic', 'process', 'where', f'ProcessId={pid}', 'get', 'CommandLine', '/format:list'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            ).stdout
        else:
            with open(f'/proc/{pid}/cmdline', 'rb') as fh:
                out = fh.read().replace(b'\x00', b' ').decode('utf-8', 'replace')
    except Exception:
        return False

    lowered = (out or '').lower()
    if 'streamlit' not in lowered:
        return False
    # 只认本项目的入口脚本，防止误杀同机上别人的 streamlit
    return 'singleengineapp' in lowered or 'engine_streamlit_app' in lowered


def _kill_pid(pid: int) -> bool:
    try:
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/PID', str(pid), '/F', '/T'],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def free_port(port: int, logger=None, dry_run: bool = False) -> list[int]:
    """清掉占用端口的本项目 streamlit 僵尸进程，返回被杀掉的 PID。"""
    killed: list[int] = []
    for pid in _netstat_pids(port):
        if pid == os.getpid():
            continue
        if not _pid_is_our_streamlit(pid):
            if logger:
                logger.warning(f"端口 {port} 被 PID {pid} 占用，但不是本项目的 streamlit，跳过")
            continue
        if dry_run:
            killed.append(pid)
            continue
        if _kill_pid(pid):
            killed.append(pid)
            if logger:
                logger.warning(f"清理端口 {port} 上的残留进程 PID {pid}")
    return killed


def port_in_use(port: int) -> bool:
    return bool(_netstat_pids(port))
