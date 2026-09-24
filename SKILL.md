---
name: image-gen
description: 通过任意 OpenAI 兼容图片接口进行文生图、图生图和文+图生图，兼容 Codex、Claude Code、OpenClaw、Hermes、OpenCode 等可执行 Node.js/Python 的客户端；服务端地址与模型名由参数或环境变量决定，不预设任何服务商。
---

# 通用生图技能

本技能包含两个零依赖可执行脚本和一份接口配方手册：

- `scripts/node/image-gen.js`：Node.js 18+，只用内置 `fetch`/`FormData`/`Blob`。
- `scripts/python/image_gen.py`：Python 3.9+，只用标准库。
- `references/api-recipes.md`：原始 HTTP 请求配方，便于移植到其他语言。
- `references/providers.md`：不同服务端/模型的参数差异。
- `README.md`：完整参数表、配置优先级、错误码与排错。
- `INSTALL.md`：安装与密钥配置。

优先安装完整目录，而不是只复制 `SKILL.md`。若客户端只能读主文件，请再显式读取脚本与 `references/`。

## 何时使用

用户要求生成、编辑、融合图片，且目标是一个 OpenAI 兼容的图片接口（官方 API、任意网关、本机推理服务）时使用本技能。先确认服务端地址与模型名；没有信息时使用脚本内置默认值（`https://api.openai.com/v1` + `gpt-image-1`）并告知用户。

## 三种模式

1. **文生图 `text`**：`POST {base}/images/generations`，JSON 体含 `prompt`。
2. **图生图 `image`**：`POST {base}/images/edits`，multipart 上传一张或多张 `image`；省略 `--prompt` 时使用默认提示词。
3. **文+图生图 `image-prompt`**：同 `edits` 端点，但同时提交 `prompt`。

`--mode edit|vision|i2i|t2i|image-to-image|text-to-image` 等别名会自动归一到上面三种。`--image` 可以是本地路径、`http(s)://` 直链或 `data:image/...;base64,...`，可重复传入。

```bash
node scripts/node/image-gen.js --mode text --prompt "生成一张太空猫" --out ./cat.png
node scripts/node/image-gen.js --mode image --image ./reference.png --out ./variation.png
node scripts/node/image-gen.js --mode image-prompt --image ./a.png --image ./b.png --prompt "融合两张参考图" --out ./result.png
python3 scripts/python/image_gen.py --mode image-prompt --images ./a.png,./b.png --prompt "融合两张参考图" --out ./result.png
```

## 指定服务端与模型

任何一项都有“命令行 > 环境变量 > `.env` > 内置默认值”四级来源：

```bash
# OpenAI 官方以外的兼容网关
node scripts/node/image-gen.js --base-url https://api.example.com/v1 --model flux-pro --prompt "海报"

# 本机推理服务（vLLM / LM Studio / Ollama 等）
IMAGE_GEN_BASE_URL=http://127.0.0.1:8000/v1 IMAGE_GEN_MODEL=sd3.5-large \
  node scripts/node/image-gen.js --prompt "城市夜景" --out ./night.png

# 只接受自定义字段的网关
python3 scripts/python/image_gen.py --prompt "Logo" --param background=transparent --header 'X-Project: demo'
```

## 参数

- `--mode text|image|image-prompt`：三种模式，`edit` 为兼容别名。
- `--api-key <key>`：最高优先级；其次 `IMAGE_GEN_API_KEY`、`OPENAI_API_KEY`、`.env`。
- `--env-file <path>`：指定 dotenv 文件；不传时按顺序读当前目录 `.env` 与用户配置文件。
- `--base-url <url>`：默认 `IMAGE_GEN_BASE_URL`/`OPENAI_BASE_URL`，否则 `https://api.openai.com/v1`。末尾 `/v1` 会自动去重。
- `--fallback-base-url <url>`：仅在 `--base-url` 网络不可达或超时时重试一次，默认不启用。
- `--model <model>`：默认 `IMAGE_GEN_MODEL`/`OPENAI_MODEL`，否则 `gpt-image-1`；原样透传，不做白名单。
- `--prompt <text>`：`text`、`image-prompt` 必填。
- `--image` / `--images`：图片路径、URL 或 data URL；`--images` 用逗号分隔。
- `--image-field <name>`：multipart 字段名，默认 `image`，某些服务端需要 `image[]`。
- `--n <number>`：数量，默认 `1`；多图输出为 `name-1.png`、`name-2.png`。
- `--out <path|dir>`：输出文件或目录，默认 `generated-image.png`。
- `--size <size>`：默认 `1024x1024`。
- `--quality <quality>`：仅在显式传入时提交，默认不提交。
- `--response-format <b64_json|url>`：仅在显式传入时提交，两种返回都能解析。
- `--stream <true|false>`：默认 `false`；服务端返回 SSE 时仍会自动识别。
- `--header <Name:value>`、`--param <key=value>`：追加请求头 / 请求体字段，可重复。
- `--timeout <seconds>`：默认 `900`。

## 密钥

1. 用 `--api-key` 传入（一次性、最优先）；
2. 或设置环境变量 `IMAGE_GEN_API_KEY`，兼容 `OPENAI_API_KEY`；
3. 或在 `.env` 写 `IMAGE_GEN_API_KEY=...`。

推荐环境变量：不落盘，适合 CI 和所有客户端。需要长期复用时放到 `~/.config/image-gen/.env`（Windows `%APPDATA%\image-gen\.env`）并 `chmod 600`；项目内 `.env` 必须进 `.gitignore`。脚本只读取已有文件，不创建、不改写、不上传密钥，错误输出也不回显密钥。

不要在聊天里索要密钥原文，也不要从 `.codex` 等客户端私有目录猜测密钥。

## 工作流程

1. 判断模式：文生图 / 图生图 / 文+图生图。
2. 确认服务端与模型来源；未知时先用默认值，并在回复里说明假设。
3. 确认密钥存在（`IMAGE_GEN_API_KEY` 或 `OPENAI_API_KEY`）；缺失时提示用户配置，不要编造。
4. 优先执行 Node 脚本；没有 Node 时用 Python 脚本。
5. 只提交必要参数；服务端严格校验时不要带 `--quality`/`--response-format`。
6. 运行后检查 stdout 的 JSON：`ok`、`paths`；失败时按 `code` 排错（见 `README.md` 错误码表）。
7. 把生成的文件路径告诉用户；需要尺寸/风格迭代时调整 `--size`、`--param` 后重试。

## 输出

成功（stdout，退出码 0）：

```json
{ "ok": true, "mode": "image-prompt", "model": "gpt-image-1", "paths": ["/abs/result.png"], "baseUrl": "https://api.example.com" }
```

失败（stderr，退出码 1）：

```json
{ "ok": false, "code": "missing_api_key", "message": "缺少 API Key。…", "detail": { "envVars": ["IMAGE_GEN_API_KEY", "OPENAI_API_KEY"] } }
```

常见错误码：`missing_api_key`、`missing_argument`、`invalid_mode`、`invalid_argument`、`image_path_not_found`、`image_url_fetch_failed`、`image_url_not_image`、`api_http_error`、`api_stream_error`、`request_timeout`、`network_error`、`no_image_result`。

## 自检

```bash
python3 tests/test_image_gen.py
```

测试在本机起 mock 图片服务，覆盖 JSON/SSE、三种模式、配置优先级与错误码，全程不联网。改动脚本后必须重跑。
