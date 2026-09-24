# 安装通用生图技能包

## 1. 获取目录

从本仓库克隆，或让客户端下载整个目录（不要只拿 `SKILL.md`）：

```bash
git clone https://github.com/<你的账号>/imageskill.git ~/.codex/skills/image-gen
```

常见安装位置：

- Codex CLI：`~/.codex/skills/image-gen`
- Claude Code：`~/.claude/skills/image-gen`
- OpenClaw / Hermes / OpenCode：各自设置中支持的 skill / rule / knowledge 目录

保持目录结构不变：`SKILL.md`、`README.md`、`INSTALL.md`、`scripts/{node,python}`、`references/`、`agents/openai.yaml`、`package.json`。

## 2. 选择运行环境

- 有 Node.js 18+：用 `scripts/node/image-gen.js`。
- 只有 Python 3.9+：用 `scripts/python/image_gen.py`。

两者参数完全一致，任选其一即可，不需要额外安装依赖。

```bash
node scripts/node/image-gen.js --help
python3 scripts/python/image_gen.py --help
```

## 3. 配置服务端与密钥

三项信息：API 根地址、模型名、密钥。都可以走命令行参数、环境变量或 `.env`。

```bash
# 临时环境变量（推荐，不落盘）
export IMAGE_GEN_BASE_URL='https://api.openai.com/v1'
export IMAGE_GEN_MODEL='gpt-image-1'
export IMAGE_GEN_API_KEY='你的密钥'
```

`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 同样生效，优先级低于对应的 `IMAGE_GEN_*` 变量。

长期复用时写入用户级配置：

```bash
# macOS / Linux
mkdir -p ~/.config/image-gen
printf 'IMAGE_GEN_BASE_URL=%s\nIMAGE_GEN_MODEL=%s\nIMAGE_GEN_API_KEY=%s\n' \
  'https://api.openai.com/v1' 'gpt-image-1' '你的密钥' > ~/.config/image-gen/.env
chmod 600 ~/.config/image-gen/.env
```

```powershell
# Windows PowerShell
$dir = Join-Path $env:APPDATA 'image-gen'
New-Item -ItemType Directory -Force $dir | Out-Null
Set-Content -Path (Join-Path $dir '.env') -Value @(
  'IMAGE_GEN_BASE_URL=https://api.openai.com/v1',
  'IMAGE_GEN_MODEL=gpt-image-1',
  'IMAGE_GEN_API_KEY=你的密钥'
)
```

读取顺序：`--env-file`/`IMAGE_GEN_ENV_FILE` → 当前目录 `.env` → `~/.config/image-gen/.env`（Windows `%APPDATA%\image-gen\.env`）→ `~/.env`（`%USERPROFILE%\.env`）。同一键名以最靠前的文件为准；脚本只读不写，也不会上传 `.env`。

密钥安全：不要把密钥粘贴到聊天记录、脚本源码或 Git 仓库；项目目录的 `.env` 必须加入 `.gitignore`。

## 4. 冒烟测试

```bash
# 离线，不需要网络和真实密钥
python3 tests/test_image_gen.py

# 在线，确认真实服务端可用
node scripts/node/image-gen.js --prompt "一只戴宇航头盔的猫" --out ./cat.png
```

## 5. 非 Codex 客户端

Hermes、OpenClaw、Claude Code、OpenCode 等只需让运行脚本的进程继承 `IMAGE_GEN_API_KEY`（或 `OPENAI_API_KEY`）。若客户端有 provider/base-url 设置，按上表同名变量填写。脚本不扫描任何客户端私有目录。

## 6. 备用地址（可选）

主地址可能 DNS 失败或超时的场景，可再配一个等价服务端：

```bash
node scripts/node/image-gen.js --base-url https://api-a.example.com/v1 \
  --fallback-base-url https://api-b.example.com/v1 --prompt "切换测试"
```

只有连接失败/超时才会切换；HTTP 4xx/5xx、鉴权失败、参数与审核错误都在主地址上直接报错。

## 7. 常见问题

- `missing_api_key`：没有密钥来源，检查环境变量或 `.env` 路径。
- `api_http_error` 401/403：密钥错误或无该模型权限。
- `api_http_error` 404 / `no_image_result`：`--base-url` 或 `--model` 不对，或服务端不提供 OpenAI 图片端点。
- `invalid_argument` 且提示未知参数：确认参数拼写，值与 `--` 之间需要空格或 `=`。
- 想确认服务端差异：看 `references/providers.md`。
