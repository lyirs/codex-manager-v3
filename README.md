# codex-register-v3-single

ChatGPT 账号无头浏览器自动批量注册工具。

支持双浏览器引擎（Playwright Chromium / Camoufox Firefox）、四家邮件服务（含通用 IMAP）、代理池轮换，asyncio 并发执行，SQLite 持久化存储，注册完成后自动完成 Codex OAuth Token 换取。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 双引擎 | Playwright (Chromium) 或 Camoufox (Firefox)，配置文件一键切换 |
| 反检测 | Chromium 注入 13 点 stealth JS；Camoufox 内置指纹混淆 + GeoIP |
| 手机指纹 | 顶层 `mobile: true` 即可全程使用手机端 UA / 视口 / 触控，注册与 OAuth 复用同一 session |
| 四家邮件 | GPTMail / NPCmail / YYDS Mail / **通用 IMAP**，统一工厂接口 |
| IMAP 别名 | qq.com / gmail.com 自动启用 `+alias` 子地址，每次注册生成唯一别名，并发互不干扰 |
| 多 IMAP 账户 | 支持配置多个 IMAP 邮箱，随机轮换或按索引固定使用 |
| OAuth Token | 注册完成后自动完成 Codex PKCE OAuth2 流程，写入 `access_token` / `refresh_token` / `id_token` |
| 代理池 | 从文本文件导入，轮询分配，失败 3 次自动禁用 |
| 并发 | asyncio.Semaphore 控制同时运行的浏览器数量 |
| 持久化 | aiosqlite 驱动的 SQLite，账号去重 upsert |
| CLI | typer + rich 美化输出，7 个子命令 |
| 日志 | loguru 双路输出：终端彩色 INFO + 文件 DEBUG（register.log，10 MB 自动轮转） |

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
| `FILL_EMAIL` | 等待邮箱输入框，用 React 原生 setter 填入生成的邮箱，点击 Continue |
| `FILL_PASSWORD` | 等待密码框，填入自动生成的强密码（大写+小写+数字+特殊字符），点击 Continue |
| `WAIT_CODE` | 轮询邮件服务获取 6 位验证码（最长 60 s，可配置） |
| `FILL_CODE` | 将 6 位验证码逐位填入 `input[maxlength="1"]` 方格或单字段，点击 Continue |
| `FILL_PROFILE` | 填写 firstName / lastName；通过 `[role="spinbutton"]` 设置出生年月日；点击 Agree |
| `COMPLETE` | 等待跳回 `chatgpt.com`，账号标记为「注册完成」；随即执行 Codex OAuth 换取 Token |

每步失败后最多重试 5 次，指数退避（网络错误最长 60 s，其他错误最长 30 s）；任何步骤检测到错误页面均自动重试。

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

# 5. 复制并编辑配置
cp config.example.yaml config.yaml
```

---

## 配置

所有配置存储在根目录的 `config.yaml`，支持 CLI 热改，也可直接用文本编辑器编辑（支持注释）。

### 完整配置示例

```yaml
# 浏览器引擎: playwright | camoufox（推荐生产环境用 camoufox）
engine: playwright
headless: true      # true=无头批量 | false=有头可见窗口（调试）
slow_mo: 0          # 操作间额外延迟ms; 0=自动（有头模式默认 80ms）
mobile: false       # true=整个注册流程使用手机端指纹（UA/视口/触控）
max_concurrent: 2

# 邮件服务: gptmail | npcmail | yydsmail | imap | imap:0 | imap:1 …
mail_provider: gptmail

mail:
  gptmail:
    api_key: "gpt-test"           # 公共 key，有频率限制
    base_url: "https://mail.chatgpt.org.uk"
  npcmail:
    api_key: ""
    base_url: "https://dash.xphdfs.me"
  yydsmail:
    api_key: ""
    base_url: "https://maliapi.215.im/v1"
  imap:                           # 支持列表，配置多个账户
    - email:    "user@gmail.com"
      password: "app-password"    # Gmail 使用应用专用密码
      host:     "imap.gmail.com"
      port:     993               # 993=IMAPS(SSL) | 143=STARTTLS
      ssl:      true
      folder:   INBOX
      # use_alias: true           # 不填则 qq.com/gmail.com 自动启用别名模式

registration:
  prefix: ""   # 邮箱前缀，留空随机生成
  domain: ""   # 邮箱域名，留空由邮件服务决定

proxy_strategy: none   # none | static | pool
proxy_static: ""       # http://user:pass@host:port（strategy=static 时生效）

# 鼠标移动模拟（减小值加速，增大值更像真人）
mouse:
  steps_min: 4
  steps_max: 8
  step_delay_min: 0.003
  step_delay_max: 0.010
  hover_min: 0.02
  hover_max: 0.08

# 各阶段超时（秒）
timeouts:
  page_load: 30
  otp_code: 180        # 轮询邮箱验证码的最长等待时间

oauth:
  enabled: true        # true=注册后自动换取 Codex Token
  timeout: 45
```

### 配置字段速查

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `engine` | `playwright` | `playwright` \| `camoufox` |
| `headless` | `true` | `false` 可在窗口中实时观察注册过程 |
| `slow_mo` | `0` | 每步额外延迟 ms，`0` = 自动 |
| `mobile` | `false` | `true` = 整个流程（含 OAuth）使用手机指纹 |
| `max_concurrent` | `2` | 最大并发浏览器数量 |
| `mail_provider` | `gptmail` | 见下方[邮件服务说明](#邮件服务说明) |
| `proxy_strategy` | `none` | `pool` \| `static` \| `none` |
| `proxy_static` | `""` | 固定代理 URL |
| `oauth.enabled` | `true` | 注册后自动换取 Codex Token |

---

## 邮件服务说明

### API 类（临时邮箱）

| 服务 | `mail_provider` 值 | 获取 Key |
|------|--------------------|---------|
| **GPTMail** | `gptmail` | 公共 Key `gpt-test` 可免费使用，有频率限制；大批量请申请付费 Key |
| **NPCmail** | `npcmail` | 注册 [dash.xphdfs.me](https://dash.xphdfs.me) 获取 |
| **YYDS Mail** | `yydsmail` | 注册 [maliapi.215.im](https://maliapi.215.im/v1) 获取 |

### IMAP（自有真实邮箱）

使用自己的 Gmail / QQ 邮箱直接收取验证码，无需第三方 API Key。

**启用方法：**

```yaml
mail_provider: imap    # 多账户时随机选取
# mail_provider: imap:0  # 固定使用第 0 个账户
# mail_provider: imap:1  # 固定使用第 1 个账户

mail:
  imap:
    - email:    "yourname@gmail.com"
      password: "abcd efgh ijkl mnop"  # Google 应用专用密码（非登录密码）
      host:     "imap.gmail.com"
      port:     993
      ssl:      true
      folder:   INBOX
    - email:    "123456@qq.com"
      password: "xxxxxxxxxxxxxx"        # QQ 邮箱授权码（非登录密码）
      host:     "imap.qq.com"
      port:     993
      ssl:      true
      folder:   INBOX
```

**别名模式（`+alias` 子地址）：**

`qq.com` 和 `gmail.com` 域名**自动启用**：每次注册生成独立别名地址（如 `yourname+a3k9xm2b@gmail.com`），验证码仍投递到原收件箱，`poll_code()` 通过 `To:` 头部过滤，多并发任务互不干扰。

其他域名如需别名模式，在账户配置中加 `use_alias: true`。

**前置步骤：**

| 邮箱 | 步骤 |
|------|------|
| Gmail | 账户安全 → 应用专用密码（生成 16 位密码）；Gmail 设置 → IMAP → 启用 |
| QQ 邮箱 | 设置 → 账户 → IMAP/SMTP 服务 → 开启，获取授权码 |

---

## CLI 命令

### 注册账号

```powershell
# 注册 5 个账号
uv run python -m src.main register --count 5

# 指定引擎和邮件服务（覆盖配置文件）
uv run python -m src.main register --count 3 --engine camoufox --provider imap:0

# 覆盖并发数
uv run python -m src.main register --count 10 --concurrency 4

# 临时指定代理（优先级最高）
uv run python -m src.main register --count 3 --proxy "http://user:pass@host:port"

# 有头模式调试（可在浏览器窗口实时观察）
uv run python -m src.main register --count 1 --headed
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
uv run python -m src.main export --format json --output accounts.json
uv run python -m src.main export --format csv  --output accounts.csv
```

### 导入账号

```powershell
uv run python -m src.main import-accounts accounts.json
uv run python -m src.main import-accounts accounts.txt   # email:password 格式
```

### 管理代理

```powershell
# 从文件导入代理（默认 proxies.txt）
uv run python -m src.main import-proxies
uv run python -m src.main import-proxies my_proxies.txt

# 启用代理池
uv run python -m src.main config set proxy_strategy pool

# 固定代理
uv run python -m src.main config set proxy_static "http://user:pass@host:port"
uv run python -m src.main config set proxy_strategy static

# 关闭代理
uv run python -m src.main config set proxy_strategy none
```

> **代理优先级**：`--proxy` CLI 参数 > `proxy_strategy=static` > `proxy_strategy=pool` > `none`

### 配置管理

```powershell
# 查看全部配置
uv run python -m src.main config show

# 读取单项
uv run python -m src.main config get engine

# 修改（自动推断 int / float / bool 类型）
uv run python -m src.main config set engine camoufox
uv run python -m src.main config set max_concurrent 4
uv run python -m src.main config set mail_provider imap
uv run python -m src.main config set headless false
uv run python -m src.main config set mobile true
uv run python -m src.main config set timeouts.otp_code 240
```

### 数据库

```powershell
uv run python -m src.main db init
```

---

## 代理文件格式

`proxies.txt` 每行一个代理：

```
http://host:port
http://user:pass@host:port
socks5://user:pass@host:port
```

---

## 项目结构

```
codex-register-v3-single/
├── config.yaml          # 运行时配置（YAML，支持注释）
├── config.example.yaml  # 配置模板（含字段说明）
├── proxies.txt          # 代理列表
├── accounts.db          # SQLite 数据库
├── register.log         # 调试日志（10 MB 自动轮转，保留 7 天）
├── pyproject.toml
├── plan/                # 只读参考资料，不要修改
│   ├── oauth.har        # 实录 OAuth 流程 HAR 包
│   └── browser/
│       └── tool.js      # 原始 JS 用户脚本（注册状态机逆向来源）
└── src/
    ├── main.py          # CLI 入口（typer）
    ├── config.py        # 配置读写（dot-notation get/set）
    ├── db.py            # SQLite schema 初始化
    ├── accounts.py      # 账号 CRUD / 导入导出
    ├── proxy_pool.py    # 代理池（轮询 + 失败计数自动禁用）
    ├── browser/
    │   ├── engine.py    # 浏览器工厂（playwright / camoufox，含手机指纹）
    │   ├── helpers.py   # DOM 工具（React input 填充、人工鼠标移动等）
    │   ├── register.py  # 7 步注册状态机
    │   └── oauth.py     # Codex PKCE OAuth2 Token 换取
    └── mail/
        ├── base.py      # 抽象基类 MailClient
        ├── gptmail.py   # GPTMail 客户端
        ├── npcmail.py   # NPCmail 客户端
        ├── yydsmail.py  # YYDS Mail 客户端
        └── imap.py      # 通用 IMAP 客户端（含 MultiIMAPMailClient）
```

---

## 开发与调试

```powershell
# 测试浏览器引擎（访问 chatgpt.com 并截图）
uv run python -m src.browser.engine playwright
uv run python -m src.browser.engine camoufox --headed

# 注册状态机空跑（不启动浏览器，仅打印 7 步日志）
uv run python -m src.browser.register

# 测试 IMAP 邮箱连通性（等待 30 s 内是否能收到验证码）
uv run python -m src.mail.imap

# 测试其他邮件服务
uv run python -m src.mail.gptmail YOUR_API_KEY
```

---

## 超时配置参考

所有超时单位为**秒**，在 `config.yaml` 的 `timeouts:` 下配置：

| 键 | 默认值 | 阶段 |
|----|--------|------|
| `page_load` | 30 | `page.goto()` 导航超时 |
| `auth0_redirect` | 8 | 等待跳转到 auth.openai.com |
| `email_input` | 15 | 等待邮箱输入框出现 |
| `password_input` | 60 | 等待密码输入框出现 |
| `otp_input` | 60 | 等待 OTP 输入框出现 |
| `otp_code` | 180 | 轮询邮箱获取验证码的最长时间 |
| `profile_detect` | 15 | 等待姓名输入框出现 |
| `complete_redirect` | 20 | 等待跳回 chatgpt.com |
| `oauth_total` | 45 | Codex OAuth 全流程硬超时 |

使用慢速代理时建议将 `page_load` 调大至 60：

```powershell
uv run python -m src.main config set timeouts.page_load 60
uv run python -m src.main config set timeouts.otp_code 240
```

---

## 依赖项

| 包 | 用途 |
|----|------|
| `playwright` | Chromium 浏览器自动化 |
| `camoufox[geoip]` | Firefox 指纹混淆引擎 |
| `httpx` | 异步 HTTP 客户端（邮件 API / OAuth） |
| `aioimaplib` | 异步 IMAP 客户端 |
| `aiosqlite` | 异步 SQLite |
| `loguru` | 结构化日志 |
| `typer` | CLI 框架 |
| `rich` | 终端美化输出 |
| `pyyaml` | YAML 配置文件解析 |

---

## 注意事项

- 本工具仅供学习研究使用，请遵守 ChatGPT 服务条款。
- 建议搭配质量稳定的代理使用，避免 IP 被封禁。
- Gmail / QQ 邮箱的 IMAP 密码须使用**应用专用密码**（授权码），而非登录密码。
- 日志文件 `register.log` 超过 10 MB 自动轮转，保留 7 天。
- 注册密码由程序自动生成（16 位，含大写+小写+数字+特殊字符），写入数据库。
- 调试时设置 `headless: false` 可在浏览器窗口中实时观察每一步操作。
