# BettaFish 本地运行改造记录

> 记录人：本地改造（Windows / Python 3.12 / MySQL 8.0.39）
> 适用范围：`D:\Aproject\github\BettaFish`，v1.2.1
> 目的：把「仓库拷下来跑不起来」变成「一条命令能起、坏了能自检」。

---

## 一、日常启动方式

### 1. 日常就这一条

```powershell
cd D:\Aproject\github\BettaFish
.\.venv\Scripts\python.exe app.py
```

> 必须用 `.\.venv\Scripts\python.exe`，**不要**用全局 `python`。
> 子应用由 app.py 用 `sys.executable` 拉起，一旦主进程跑在全局解释器上，8501/8502/8503 也会用全局解释器，依赖全丢。

用 `.venv` 里的 python 启动后，浏览器打开 <http://127.0.0.1:5000>，点页面上的启动按钮，三个分析 Agent 才会被拉起来（`app.py` 本身只起 Flask，不自动起子应用）。

### 2. 换机器 / 重装依赖时用启动器

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_bettafish.ps1
```

或双击 `run_bettafish.bat`。它会依次做：找 Python → 建 `.venv` → 装依赖 → 装 playwright chromium → 写 `.env` → 建库建表 → `python app.py`。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `-SetupOnly` | 只装环境，不启动 |
| `-SkipPlaywright` | 跳过 chromium 下载（约 150MB） |
| `-Full` | 装全量 `requirements.txt`（含 weasyprint/xgboost/opencv 等） |
| `-DbMode mysql` | 明确使用本机 MySQL |
| `-DbMode docker` | 用 docker 起 postgres:15 |

> ⚠️ 参数名是 `-DbMode`，不能写 `-Db`：`[CmdletBinding()]` 会自动注入通用参数 `-Debug`，其别名 `-db` 与 `-Db` 大小写不敏感重名，PowerShell 直接报「参数冲突」。

### 3. 出问题先体检

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

只读检查：Python 版本、15 个关键包版本、四条 import 链（config / MindSpider / ReportEngine / ForumEngine）、四个端口占用、`.env` 关键项、MySQL 连通性。结尾给 SUMMARY，FAIL 项直接对应修法。

---

## 二、本机环境事实

| 项 | 值 |
| --- | --- |
| 解释器 | Python 3.12（`C:\Users\Administrator\AppData\Local\Programs\Python\Python312`） |
| 虚拟环境 | `D:\Aproject\github\BettaFish\.venv` |
| 数据库 | 本机 MySQL 8.0.39，`127.0.0.1:3306`，库名 `bettafish`（utf8mb4 / utf8mb4_unicode_ci） |
| 主服务 | Flask + flask-socketio，`127.0.0.1:5000` |
| 子应用 | insight `8501` / media `8502` / query `8503`（Streamlit，由 app.py 拉起） |
| LLM 网关 | `https://jpn.cdn.moxuna.com/v1`（OpenAI 兼容，new-api） |

---

## 三、改动清单

### 3.1 新增文件

| 文件 | 作用 |
| --- | --- |
| `requirements-core.txt` | 精简依赖集：只装能跑起来的部分，避开 weasyprint / xgboost / opencv 这些重型或需系统库的包 |
| `run_bettafish.ps1` | 六步启动器（找 python → venv → pip → playwright → .env → 建库建表 → 起 app.py） |
| `run_bettafish.bat` | 双击入口，`chcp 65001` 后调用 ps1 |
| `scripts/init_mysql_db.py` | 只负责 `CREATE DATABASE IF NOT EXISTS bettafish`，project 自带的 init_database.py 只建表不建库 |
| `scripts/doctor.py` | 启动自检，只读 |
| `.env` | 本地配置（**被 .gitignore 忽略，不入库**） |

### 3.2 `app.py`（3 处）

| 位置 | 改动 | 原因 |
| --- | --- | --- |
| `app.py:296` | `wait_for_app_startup(app_name, 30)` → `120` | insight 冷启动要 import torch + sentence-transformers，30 秒不够，会被误判「进程启动失败」然后 `cleanup_processes()` 连坐全杀 |
| `app.py:331` | ReportEngine 初始化失败只 `logger.warning`，不再 `errors.append` | 原来 `REPORT_ENGINE_API_KEY` 为空时 `ReportEngine/llms/base.py:46` 抛 `ValueError`，被当成致命错误 → `/api/system/start` 返回 500 并且把已经起来的三个 Agent 全杀 |
| `app.py:831` | 新增 `read_app_log_tail(app_name, lines=12)`；`app.py:299` 失败时把日志末行带进 errors | 原来前端只能看到「进程启动失败」，真实 traceback 埋在 `logs/<app>.log` 里看不到 |
| `app.py:1286` | `/api/system/start` 失败时 message 带上 `errors[0]` | 前端 `index.html:2982` 只显示 `data.message`，不带明细就等于只说「系统启动失败」 |

### 3.3 `MindSpider/main.py`（1 处）

| 位置 | 改动 | 原因 |
| --- | --- | --- |
| `MindSpider/main.py:164` | `subprocess.run(..., encoding='utf-8')` 增加 `errors='replace'` | Windows 下子进程（SQLAlchemy/loguru）吐 GBK 字节，严格 utf-8 解码让读取线程抛 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xca in position 203` |

### 3.4 `requirements-core.txt`

```diff
- numpy>=1.24.0,<2.0
+ numpy>=2.0.0,<2.4
+ pyarrow>=14.0.0
```

**这是最关键的一处。** 钉 `numpy<2` 的后果不是警告，是 Streamlit 直接 ImportError 退出：

```
ImportError: pyarrow requires NumPy 2.0 or newer, found 1.26.4
```

调用链：`streamlit/__init__.py:56` → `delta_generator.py:37` → `runtime/caching/cache_errors.py:18` → `streamlit/type_util.py:42 import pyarrow as pa` → `pyarrow/lib.pyx:42`。

pip 会提示 `streamlit 1.28.1 requires numpy<2,>=1.19.3` —— 那是包元数据，实际 import 可用，忽略即可。

---

## 四、踩过的坑（按出现顺序）

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | `无法指定参数"Db"，因为它与参数"Debug"的同名参数别名冲突` | `[CmdletBinding()]` 注入 `-Debug`，其别名 `-db` 与自定义 `-Db` 重名 | 参数改名 `-DbMode`，加 `[Alias('Database','DbKind')]` |
| 2 | 三个 Streamlit 子应用起来就秒退，`/api/system/start` 返回 500 | numpy 1.26.4 与 pyarrow 26 冲突 | `numpy>=2.0.0,<2.4` + `pyarrow>=14.0.0` |
| 3 | 缺 ReportEngine key 时整个系统启动失败，已起的子进程被 `cleanup_processes()` 杀掉 | `REPORT_ENGINE_API_KEY` 空 → `ValueError` → `errors` 非空 → 整体回滚 | ReportEngine 降级为 warning，不阻断（`app.py:331`） |
| 4 | `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xca` | Windows GBK 输出被严格 utf-8 解码 | `errors='replace'`（`MindSpider/main.py:164`） |
| 5 | 前端只显示「系统启动失败」，看不到原因 | `/api/system/start` 的 message 是固定文案 | `read_app_log_tail()` + message 带 `errors[0]` |
| 6 | Insight 跑到 `report_structure_node` 报 `openai.NotFoundError: Error code: 404` | `*_BASE_URL` 写成 `https://jpn.cdn.moxuna.com`，缺 `/v1`，openai SDK 拼成 `/chat/completions` → 网关 404 | 六组 BASE_URL 全部改成 `.../v1` |
| 7 | `curl ... ^ -H ...` 报 `-H 项无法识别`、`invalid JSON request body` | PowerShell 续行符是反引号 `` ` ``，不是 cmd 的 `^` | 改用反引号或 `Invoke-RestMethod` |

**坑 6 的判断方法**（不用翻文档就能确认路径对不对）：

```powershell
curl.exe -s https://jpn.cdn.moxuna.com/models      # → 404 Not Found (openresty)
curl.exe -s https://jpn.cdn.moxuna.com/v1/models   # → 401 Invalid token (new_api_error)
```

401 说明路径存在（只是没带 key），404 说明路径不对。

---

## 五、API 配置说明

`.env` 里六组 LLM 配置，各自独立，缺哪组只影响对应功能：

| 配置组 | 用途 | 当前模型 |
| --- | --- | --- |
| `INSIGHT_ENGINE_*` | 私有库挖掘 Agent（8501） | `deepseek-v4.1-flash` |
| `MEDIA_ENGINE_*` | 多模态 Agent（8502） | `deepseek-v4.1-flash` |
| `QUERY_ENGINE_*` | 网页搜索 Agent（8503） | `deepseek-v4.1-flash` |
| `REPORT_ENGINE_*` | 报告生成（图表/长段落，对模型能力要求高） | `deepseek-v4-pro` |
| `FORUM_HOST_*` | 论坛主持人（多方讨论） | `deepseek-v4.1-flash` |
| `KEYWORD_OPTIMIZER_*` | SQL 关键词优化 | `deepseek-v4.1-flash` |
| `MINDSPIDER_*` | 爬虫关键词/摘要 | 未配置（可留空） |

网关可用模型（`/v1/models` 实测）：`deepseek-flash`、`deepseek-v4.1-flash`、`deepseek-v4-pro`。

**搜索工具**（只影响 Media / Query 两个 Agent，Insight 不用）：

- `SEARCH_TOOL_TYPE` 只支持 `AnspireAPI` / `BochaAPI` 两种，不支持 Tavily 作为主搜索。
- 当前 `ANSPIRE_API_KEY` 为空 → 跑 Media/Query 的搜索阶段会报缺 key。二选一：
  - 去 <https://plugin.anspire.cn> 拿 key 填 `ANSPIRE_API_KEY`
  - 去 <https://open.bochaai.com> 拿 key 填 `BOCHA_WEB_SEARCH_API_KEY`，并把 `SEARCH_TOOL_TYPE` 改成 `BochaAPI`
- `TAVILY_API_KEY` 已配置，QueryEngine 的部分链路会用。

---

## 六、已知限制

| 项 | 影响 | 说明 |
| --- | --- | --- |
| weasyprint 未安装 | PDF 报表不可用 | `ReportEngine/renderers/pdf_renderer.py:42` 是延迟 import，缺了只丢 PDF，HTML 报表正常。Windows 装它需要 GTK 运行库，不建议在核心依赖里带 |
| eventlet 未安装 | 仅一行 debug 日志 | `app.py:41` 的断开补丁会跳过，Flask 用默认 WSGI |
| 数据库目前是空表 | Insight 结论会是「无相关舆情」 | InsightEngine 的搜索工具全部查本地库（`InsightEngine/tools/search.py`），库里没数据就是没结果，需要先跑 MindSpider 抓 |
| `MindSpider/DeepSentimentCrawling/` 缺失 | 深度情感爬取不可用 | `.gitmodules` 里声明为子模块（MediaCrawler），但目录不存在，主系统不依赖它 |
| `scripts/doctor.py` 的 ABI 提示 | 无 | 若 `numpy.core.multiarray failed to import`，重装 `pandas` 即可 |

---

## 七、已验证的链路

1. ✅ MySQL 建库建表：`database bettafish ready (utf8mb4 / utf8mb4_unicode_ci)`，`数据库初始化成功`
2. ✅ Flask 起在 `http://127.0.0.1:5000`，前端 socket.io 轮询正常
3. ✅ ReportEngine 接口注册（`ReportEngine接口已注册`）
4. ✅ 三个 Streamlit 子应用通过健康检查 `/_stcore/health`
5. ✅ Insight Agent 端到端跑到 LLM 调用
6. ✅ 网关鉴权通过，`deepseek-v4.1-flash` 模型存在，`/v1` 路径正确
7. ⏳ 完整一轮舆情分析（需要库里有数据 + 搜索 key）
