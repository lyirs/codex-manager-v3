# codex-register-v3-single

ChatGPT 账号无头浏览器自动批量注册工具。

支持双浏览器引擎（Playwright Chromium / Camoufox Firefox）、三家临时邮件服务、代理池轮换，asyncio 并发执行，SQLite 持久化存储。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 双引擎 | Playwright (Chromium) 或 Camoufox (Firefox)，配置文件切换 |
| 反检测 | Chromium 注入 stealth JS；Camoufox 内置指纹混淆 + GeoIP |
| 三家邮件 | GPTMail / NPCmail / YYDS Mail，通过工厂函数统一接口 |
| 代理池 | 从文本文件导入，轮询分配，失败 3 次自动禁用 |
| 并发 | asyncio.Semaphore 控制同时运行的浏览器数量 |
| 持久化 | aiosqlite 驱动的 SQLite，账号去重 upsert |
| CLI | typer + rich 美化输出，7 个子命令 |
| 日志 | loguru 双路输出：终端彩色 INFO + 文件 DEBUG（register.log） |

---

## 注册流程（7 步状态机）

注册流程完全逆向自 `plan/browser/tool.js` (`_0x548_inner`) 并在 Python 中忠实复现：

```
GOTO_SIGNUP → FILL_EMAIL → FILL_PASSWORD → WAIT_CODE
            → FILL_CODE  → FILL_PROFILE  → COMPLETE
```

| 状态 | 动作 |
|------|------|
| `GOTO_SIGNUP` | 打开 `chatgpt.com/auth/login`，等待 Auth0 跳转；若邮箱框已出现直接填写，否则查找并点击"Sign up"按钮 |
| `FILL_EMAIL` | 等待邮箱输入框（≤15 s），用 React 原生 setter 填入生成的邮箱，点击 Continue |
| `FILL_PASSWORD` | 等待密码框（≤60 s），填入自动生成的强密码（大写+小写+数字+特殊字符），点击 Continue |
| `WAIT_CODE` | 轮询 gptmail `/emails?email=…` 接口（每 3 s 一次，最长 3 分钟），提取 6 位验证码 |
| `FILL_CODE` | 将 6 位验证码逐位填入 `input[maxlength="1"]` 方格或单字段，点击 Continue |
| `FILL_PROFILE` | 填写 firstName / lastName；通过 `[role="spinbutton"]` 设置出生年月日；点击 Agree |
| `COMPLETE` | 等待跳回 `chatgpt.com`，账号标记为「注册完成」 |

每步失败后最多重试 5 次，退避间隔线性增长；任何步骤检测到错误页面（糟糕 / 出错了 / Operation timed out）均自动重试。

---

## 环境要求

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器

---

## 安装

```powershell
# 1. 克隆项目
git clone <repo-url>
cd codex-register-v3-single

# 2. 安装依赖
uv sync

# 3. 安装浏览器
uv run python -m playwright install chromium
uv run python -m camoufox fetch   # 下载 GeoIP 数据库（约 65 MB）

# 4. 初始化数据库
uv run python -m src.main db init
```

---

## 配置

所有配置存储在根目录的 `config.yaml`，支持 CLI 热改，也可直接用文本编辑器编辑（支持注释）。

```yaml
# 浏览器引擎: playwright | camoufox
engine: playwright
# true = 无头批量模式 | false = 有头可见窗口（调试用）
headless: true
# 操作间额外延迟(毫秒); 0 = 自动（有头模式默认 80ms）
slow_mo: 0
max_concurrent: 2

# 邮件服务: gptmail | npcmail | yydsmail
mail_provider: gptmail

mail:
  gptmail:
    api_key: "YOUR_GPTMAIL_KEY"     # 公共 key: gpt-test（有频率限制）
    base_url: "https://mail.chatgpt.org.uk"
  npcmail:
    api_key: "YOUR_NPCMAIL_KEY"
    base_url: "https://dash.xphdfs.me"
  yydsmail:
    api_key: "YOUR_YYDSMAIL_KEY"
    base_url: "https://maliapi.215.im/v1"

registration:
  prefix: ""   # 留空则随机生成 12 位
  domain: ""   # 留空则由邮件服务决定

# 代理策略: pool | static | none
proxy_strategy: none
proxy_static: ""  # 固定代理 URL，proxy_strategy=static 时生效

# 预留配置（暂未实现）
team:
  url: ""
  key: ""
sync:
  url: ""
  key: ""
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `engine` | `playwright` | 浏览器引擎：`playwright` \| `camoufox` |
| `headless` | `true` | `true` = 无头批量；`false` = 有头可见窗口（调试） |
| `slow_mo` | `0` | 每步额外延迟 ms；`0` = 自动（有头模式默认 80ms） |
| `max_concurrent` | `2` | 最大同时运行的浏览器数量 |
| `mail_provider` | `gptmail` | 邮件服务：`gptmail` \| `npcmail` \| `yydsmail` |
| `proxy_strategy` | `none` | 代理策略：`pool` \| `static` \| `none` |
| `proxy_static` | `""` | 固定代理 URL，`proxy_strategy=static` 时生效 |
| `registration.prefix` | `""` | 邮箱前缀，留空则随机生成 |
| `registration.domain` | `""` | 邮箱域名，留空则由邮件服务决定 |

---

## 代理文件格式

`proxies.txt` 每行一个代理，支持以下格式：

```
http://host:port
http://user:pass@host:port
socks5://user:pass@host:port
```

---

## CLI 命令

### 注册账号

```powershell
# 注册 5 个账号（使用配置文件中的引擎和邮件服务）
uv run python -m src.main register --count 5

# 指定引擎和邮件服务
uv run python -m src.main register --count 3 --engine camoufox --provider npcmail

# 覆盖并发数
uv run python -m src.main register --count 10 --concurrency 4

# 一次性指定静态代理（优先级最高，覆盖配置文件）
uv run python -m src.main register --count 3 --proxy "http://user:pass@host:port"
```

### 查看账号

```powershell
# 列出全部账号
uv run python -m src.main list-accounts

# 按状态筛选
uv run python -m src.main list-accounts --status 注册完成
```

### 导出账号

```powershell
# 导出为 JSON
uv run python -m src.main export --format json --output accounts.json

# 导出为 CSV
uv run python -m src.main export --format csv --output accounts.csv
```

### 导入账号

```powershell
# 从 JSON 文件导入
uv run python -m src.main import-accounts accounts.json

# 从 email:password 文本文件导入
uv run python -m src.main import-accounts accounts.txt
```

### 管理代理

```powershell
# ── 代理池模式 ──────────────────────────────────────────
# 从默认文件 proxies.txt 导入
uv run python -m src.main import-proxies

# 指定文件
uv run python -m src.main import-proxies my_proxies.txt

# 启用代理池
uv run python -m src.main config set proxy_strategy pool

# ── 固定代理模式 ────────────────────────────────────────
# 配置一个固定代理并启用
uv run python -m src.main config set proxy_static "http://user:pass@host:port"
uv run python -m src.main config set proxy_strategy static

# ── 关闭代理 ────────────────────────────────────────────
uv run python -m src.main config set proxy_strategy none
```

> **代理策略优先级**：`--proxy` CLI 参数 > `proxy_strategy=static` > `proxy_strategy=pool` > `none`

### 配置管理

```powershell
# 查看全部配置
uv run python -m src.main config show

# 读取单个配置项
uv run python -m src.main config get engine

# 修改配置项（支持 int / float / bool 自动类型推断）
uv run python -m src.main config set engine camoufox
uv run python -m src.main config set max_concurrent 4
uv run python -m src.main config set mail.gptmail.api_key YOUR_KEY

# 开启有头模式（可在浏览器窗口中观察注册过程）
uv run python -m src.main config set headless false
```

### 数据库

```powershell
uv run python -m src.main db init
```

---

## 邮件服务说明

| 服务 | API 端点 | 获取 Key |
|------|----------|---------|
| **GPTMail** | `https://mail.chatgpt.org.uk` | 公共 Key `gpt-test` 可免费使用，有频率限制；大批量建议申请付费 Key |
| **NPCmail** | `https://dash.xphdfs.me` | 需注册获取 API Key |
| **YYDS Mail** | `https://maliapi.215.im/v1` | 需注册获取 API Key |

---

## 项目结构

```
codex-register-v3-single/
├── config.yaml          # 运行时配置（YAML，支持注释）
├── config.example.yaml  # 配置模板（含字段说明，复制后改名使用）
├── proxies.txt          # 代理列表
├── accounts.db          # SQLite 数据库
├── register.log         # 调试日志（自动轮转）
├── pyproject.toml
├── plan/
│   ├── oauth.har        # 实录 OAuth 流程 HAR 包（用于逆向参考）
│   └── browser/
│       └── tool.js      # 原始 JS 用户脚本（注册状态机逆向来源）
└── src/
    ├── main.py          # CLI 入口（typer）
    ├── config.py        # 配置读写（dot-notation）
    ├── db.py            # SQLite 初始化
    ├── accounts.py      # 账号 CRUD / 导入导出
    ├── proxy_pool.py    # 代理池（轮询 + 失败追踪）
    ├── browser/
    │   ├── engine.py    # 浏览器工厂（playwright / camoufox）
    │   ├── helpers.py   # DOM 工具（React input 填充、等待元素等）
    │   └── register.py  # 7 步注册状态机（忠实复现 tool.js _0x548_inner）
    └── mail/
        ├── base.py      # 抽象基类 MailClient
        ├── gptmail.py   # GPTMail 客户端
        ├── npcmail.py   # NPCmail 客户端
        └── yydsmail.py  # YYDS Mail 客户端
```

---

## 开发与调试

```powershell
# 单独测试浏览器引擎（访问 chatgpt.com 并截图）
uv run python -m src.browser.engine playwright
uv run python -m src.browser.engine camoufox

# 有头模式调试（可见浏览器窗口，操作间自动加 80ms 延迟）
uv run python -m src.browser.engine playwright --headed

# 状态机空跑（不启动浏览器，仅打印 7 步日志）
uv run python -m src.browser.register

# 测试邮件服务连通性（会向真实 API 发请求）
uv run python -m src.mail.gptmail YOUR_API_KEY
```

---

## 依赖项

| 包 | 用途 |
|----|------|
| `playwright` | Chromium 浏览器自动化 |
| `camoufox[geoip]` | Firefox 指纹混淆引擎 |
| `httpx` | 异步 HTTP 客户端（邮件 API） |
| `aiosqlite` | 异步 SQLite |
| `loguru` | 结构化日志 |
| `typer` | CLI 框架 |
| `rich` | 终端美化输出 |
| `pyyaml` | YAML 配置文件解析 |

---

## 注意事项

- 本工具仅供学习研究使用，请遵守 ChatGPT 服务条款。
- 建议搭配质量稳定的代理池使用，避免 IP 被封禁。
- GPTMail 公共 Key `gpt-test` 有严格的频率限制，大批量注册请使用付费 Key 或其他邮件服务。
- 日志文件 `register.log` 超过 10 MB 自动轮转，保留 7 天。
- 注册过程中密码由程序自动生成（16 位，含大写+小写+数字+特殊字符），无需手动填写。
- 调试时建议设置 `headless: false`，可在浏览器窗口中实时观察每一步操作。
