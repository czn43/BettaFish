#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探测 .env 里配置的 LLM 网关：列出模型、逐个试 chat。

用法（项目根目录）:
    .\\.venv\\Scripts\\python.exe scripts\\probe_llm.py
    .\\.venv\\Scripts\\python.exe scripts\\probe_llm.py --prefix REPORT_ENGINE
    .\\.venv\\Scripts\\python.exe scripts\\probe_llm.py --models a,b,c

不打印 key，只打印脱敏后的前 12 位。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 控制台可能是 GBK，模型回复里常有 emoji（比如 🏓），直接 print 会抛
# UnicodeEncodeError 把探测本身打断。先兜住。
try:
    sys.path.insert(0, str(ROOT))
    from utils.safe_console import install_safe_print
    install_safe_print()
except Exception:
    pass


def safe_text(value, limit: int = 60) -> str:
    """把模型回复压成一行、可打印的短文本。"""
    try:
        text = '' if value is None else str(value)
    except Exception:
        return '<unprintable>'
    text = text.replace('\n', ' ').replace('\r', ' ').strip()
    text = text.encode('utf-8', 'replace').decode('utf-8', 'replace')
    return text[:limit]


def read_env() -> dict[str, str]:
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


def mask(key: str) -> str:
    if not key:
        return '<empty>'
    return key[:12] + '...' + key[-4:] if len(key) > 20 else key[:6] + '...'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefix', default='REPORT_ENGINE', help='读哪组配置，默认 REPORT_ENGINE')
    ap.add_argument('--models', default='', help='逗号分隔的模型名，覆盖 .env 里的 MODEL_NAME')
    args = ap.parse_args()

    env = read_env()
    api_key = env.get(f'{args.prefix}_API_KEY', '')
    base_url = env.get(f'{args.prefix}_BASE_URL', '')
    model = env.get(f'{args.prefix}_MODEL_NAME', '')

    print(f'config prefix : {args.prefix}')
    print(f'base_url      : {base_url}')
    print(f'api_key       : {mask(api_key)}')
    print(f'model         : {model}')
    print()

    if not api_key:
        print('FAIL: API key 为空')
        return 2

    try:
        from openai import OpenAI
    except Exception as exc:
        print(f'FAIL: openai 未安装: {exc}')
        return 2

    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    # 1) 列出可用模型
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f'available models ({len(ids)}):')
        for mid in ids:
            print(f'  - {mid}')
    except Exception as exc:
        ids = []
        print(f'WARN: /models 列表获取失败: {type(exc).__name__}: {exc}')
    print()

    # 2) 逐个试 chat（非流式，短输出）
    targets = [m.strip() for m in args.models.split(',') if m.strip()] or ([model] if model else [])
    if ids:
        for mid in ids:
            if mid not in targets:
                targets.append(mid)

    print(f'chat probe ({len(targets)} models):')
    rc = 0
    for mid in targets:
        try:
            resp = client.chat.completions.create(
                model=mid,
                messages=[{'role': 'user', 'content': 'ping'}],
                max_tokens=8,
            )
            text = safe_text(resp.choices[0].message.content, 40)
            print(f'  [ OK ] {mid:<28} -> {text!r}')
        except Exception as exc:
            print(f'  [FAIL] {mid:<28} -> {type(exc).__name__}: {safe_text(exc, 120)}')
            if mid == model:
                rc = 1

    # 3) 流式试一次（ReportEngine 走的是 stream=True，有些网关非流式能过、流式会炸）
    if targets:
        probe = model if model in targets else targets[0]
        try:
            stream = client.chat.completions.create(
                model=probe,
                messages=[{'role': 'user', 'content': 'ping'}],
                max_tokens=8,
                stream=True,
            )
            got = ''
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    got += chunk.choices[0].delta.content
            print(f'\n  [ OK ] stream {probe} -> {safe_text(got, 40)!r}')
        except Exception as exc:
            print(f'\n  [FAIL] stream {probe} -> {type(exc).__name__}: {safe_text(exc, 120)}')
            rc = 1

    return rc


if __name__ == '__main__':
    raise SystemExit(main())
