# image-gen：通用流式生图技能包

一个不绑定任何服务商的 OpenAI 兼容图片生成技能包，包含两个零依赖可执行脚本（Node.js 与 Python）、一份技能说明、一份接口配方手册和一套离线回归测试。

- **三种模式**：文生图（`text`）、图生图（`image`）、文+图生图（`image-prompt`）。
- **任意服务端**：OpenAI 官方、任意 OpenAI 兼容网关、本机推理服务（vLLM、LM Studio、Ollama、ComfyUI 桥接等），只要提供 `/v1/images/generations` 与 `/v1/images/edits`。
- **任意模型**：模型名只是命令行/环境变量里的一个字符串，不做白名单校验。
- **零依赖**：Node.js 18+ 只用内置 `fetch`/`FormData`/`Blob`；Python 3.9+ 只用标准库。
- **响应自适应**：普通 JSON 与 SSE 流式两种返回都能解析，渐进图（partial image）自动跳过。

## 目录结构

```text
image-gen/
├── SKILL.md                    # 技能主文件，供 Codex / Claude Code 等客户端读取
├── README.md                   # 本文档：安装、配置、参数、排错
├── INSTALL.md                  # 精简安装说明（面向自动化客户端）
├── package.json                # 声明 CommonJS，避免 .js 被当作 ESM
├── scripts/
│   ├── node/image-gen.js       # Node.js 实现
│   └── python/image_gen.py     # Python 实现
├── references/
│   ├── api-recipes.md          # 原始 HTTP 请求配方，便于移植到其他语言
│   └── providers.md            # 各类服务端/模型的接入参数差异
├── agents/openai.yaml          # 客户端界面元数据
└── tests/test_image_gen.py     # 离线回归测试（本机 mock 服务，不联网）
```

## 快速开始

```bash
export OPENAI_API_KEY='你的密钥'          # 或 IMAGE_GEN_API_KEY
node scripts/node/image-gen.js --prompt "一只戴宇航头盔的猫" --out ./cat.png
```

```bash
# 没有 Node.js 时用 Python
python3 scripts/python/image_gen.py --prompt "一只戴宇航头盔的猫" --out ./cat.png
```

指定服务端与模型（示例：本机 vLLM）：

```bash
node scripts/node/image-gen.js \
  --base-url http://127.0.0.1:8000/v1 \
  --model sd3.5-large \
  --prompt "城市夜景，霓虹灯，湿反射地面" \
  --out ./night.png
```

图生图 / 文+图生图：

```bash
node scripts/node/image-gen.js --mode image --image ./reference.png --out ./variation.png
node scripts/node/image-gen.js --mode image-prompt --image ./a.png --image ./b.png \
  --prompt "融合两张参考图，保留主体，改成赛博朋克风格" --out ./result.png
python3 scripts/python/image_gen.py --mode image-prompt --images ./a.png,./b.png --prompt "融合两张参考图"
```

## 安装

### 作为技能目录安装

把整个目录放到客户端的技能目录，目录结构保持不变：

| 客户端 | 位置 |
| --- | --- |
| Codex CLI | `~/.codex/skills/image-gen/` |
| Claude Code | `~/.claude/skills/image-gen/` |
| OpenClaw / Hermes / OpenCode | 各自设置里指定的 skill / rule / knowledge 目录 |

请安装完整目录，而不是只复制 `SKILL.md`；若客户端只能读主文件，需再显式读取脚本与 `references/`。

### 作为命令行工具直接跑

```bash
git clone https://github.com/<你的账号>/imageskill.git
cd imageskill
node scripts/node/image-gen.js --help
python3 scripts/python/image_gen.py --help
```

## 配置

### 优先级

每个配置项都遵循同一条链路：

```text
命令行参数  >  进程环境变量  >  .env 文件  >  脚本内置默认值
```

内置默认值只有两组：`https://api.openai.com/v1`（基址）与 `gpt-image-1`（模型）。它们是可直接替换的兜底值，不构成任何服务商绑定。

### 环境变量

| 变量 | 等价参数 | 说明 |
| --- | --- | --- |
| `IMAGE_GEN_API_KEY` | `--api-key` | 首选密钥变量；缺失时回退 `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | `--api-key` | 兼容变量，多数客户端已有 |
| `IMAGE_GEN_BASE_URL` | `--base-url` | API 根地址，带不带 `/v1` 都能识别 |
| `OPENAI_BASE_URL` | `--base-url` | 兼容变量，优先级低于 `IMAGE_GEN_BASE_URL` |
| `IMAGE_GEN_MODEL` | `--model` | 图片模型名，原样透传 |
| `OPENAI_MODEL` | `--model` | 兼容变量 |
| `IMAGE_GEN_FALLBACK_BASE_URL` | `--fallback-base-url` | 备用地址，默认不启用 |
| `IMAGE_GEN_SIZE` | `--size` | 默认 `1024x1024` |
| `IMAGE_GEN_QUALITY` | `--quality` | 默认不提交该字段 |
| `IMAGE_GEN_RESPONSE_FORMAT` | `--response-format` | 默认不提交该字段 |
| `IMAGE_GEN_STREAM` | `--stream` | 默认 `false` |
| `IMAGE_GEN_IMAGE_FIELD` | `--image-field` | multipart 图片字段名，默认 `image` |
| `IMAGE_GEN_PROMPT` | 图生图默认提示词 | `--mode image` 且未写 `--prompt` 时使用 |
| `IMAGE_GEN_ENV_FILE` | `--env-file` | 指定 dotenv 文件路径 |

### .env 文件位置

按顺序尝试，同一键名以最靠前的文件为准：

1. `--env-file` / `IMAGE_GEN_ENV_FILE` 指定的文件（不存在会直接报错，不静默跳过）；
2. 当前工作目录 `.env`；
3. 用户级配置：macOS/Linux `~/.config/image-gen/.env`（支持 `XDG_CONFIG_HOME`）、Windows `%APPDATA%\image-gen\.env`；
4. `~/.env` 或 `%USERPROFILE%\.env`。

```bash
mkdir -p ~/.config/image-gen
printf 'IMAGE_GEN_BASE_URL=%s\nIMAGE_GEN_API_KEY=%s\n' 'https://api.example.com/v1' '你的密钥' > ~/.config/image-gen/.env
chmod 600 ~/.config/image-gen/.env
```

安全约定：脚本只读取 `.env`，不会创建、改写或上传它；错误输出只包含状态码与响应体，不回显密钥。项目内的 `.env` 必须写进 `.gitignore`。

## 参数说明

两个脚本参数完全一致。

| 参数 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--mode` | `text` / `image` / `image-prompt` | `text` | `edit`、`vision`、`i2i`、`t2i` 等别名自动归一 |
| `--api-key` | 字符串 | 空 | 优先级最高的密钥来源 |
| `--env-file` | 路径 | 空 | 指定 dotenv 文件 |
| `--base-url` | URL | `https://api.openai.com/v1` | API 根地址 |
| `--fallback-base-url` | URL | 空 | 主地址网络不可达/超时时的备用地址 |
| `--model` | 字符串 | `gpt-image-1` | 模型名，原样提交 |
| `--prompt` | 字符串 | 空 | `text`、`image-prompt` 必填 |
| `--image` | 路径/URL/data URL | — | 可重复传入多张 |
| `--images` | 逗号分隔列表 | — | 与 `--image` 合并去空 |
| `--image-field` | 字符串 | `image` | 有的服务端要求 `image[]` |
| `--n` | 正整数 | `1` | 生成数量 |
| `--out` | 路径或目录 | `generated-image.png` | 以 `/` 结尾或无扩展名视为目录 |
| `--size` | 字符串 | `1024x1024` | 也可 `1536x1024`、`1024x1536`，取决于服务端 |
| `--quality` | 字符串 | 不提交 | 严格校验的服务端可能拒绝该字段，故默认省略 |
| `--response-format` | `b64_json` / `url` | 不提交 | 省略时由服务端决定，脚本两种都能解析 |
| `--stream` | `true` / `false` | `false` | 服务端即使忽略该字段，返回 SSE 也能识别 |
| `--header` | `Name: value` | — | 追加请求头，可重复 |
| `--param` | `key=value` | — | 追加任意请求体字段，值支持 JSON/布尔/数字 |
| `--timeout` | 秒 | `900` | 连接与读取共用 |
| `--help` | — | — | 打印用法 |

`--param` 常用示例：

```bash
# OpenAI 背景透明、output_format
node scripts/node/image-gen.js --prompt "Logo 草图" --param background=transparent --param output_format=png

# 服务端私有字段（嵌套对象用 JSON）
python3 scripts/python/image_gen.py --prompt "海报" --param 'extra={"style":"flat","seed":42}'

# 某些网关要求的额外请求头
node scripts/node/image-gen.js --prompt "测试" --header 'X-Project: demo'
```

## 请求与响应细节

### 端点拼装

- `text` → `POST {base}/images/generations`，`application/json`；
- `image` / `image-prompt` → `POST {base}/images/edits`，`multipart/form-data`；
- `--base-url` 末尾的 `/v1` 会自动去重：`https://api.example.com` 与 `https://api.example.com/v1` 都会得到正确的 `/v1/images/...`；
- 只提交必要字段：`model`、`n`、`size`、`prompt`（+ 显式 `--param`），`quality`/`response_format`/`stream` 仅在显式配置时出现，兼容性最好。

### 图片入参

`--image` 支持三种写法，可在一次调用里混用：

1. 本地路径：按扩展名推断 `Content-Type`，未知时按 `image/png`；
2. `http(s)://`：先下载（非 `image/*` 直接报 `image_url_not_image`），再作为文件上传；
3. `data:image/png;base64,...`：本地解码后上传。

多张图片重复写入同一个 multipart 字段（默认字段名 `image`），不存在的本地路径报 `image_path_not_found`。

### 结果解析

脚本递归扫描响应对象里的 `b64_json` / `base64` / `image_base64` / `partial_image_b64` 与 `url` / `image_url` / `result_url`，并继续下钻 `data` / `images` / `output` / `results` / `artifacts` / `item` / `response.output`，因此以下形态都能取到图：

- 标准 JSON：`{"data":[{"b64_json":"..."}]}`；
- 流式事件：`{"type":"image_generation.completed","b64_json":"..."}`；
- Responses 风格：`{"type":"response.output_item.added","item":{"type":"image_generation_call","result":"..."}}`；
- 只返回外链：脚本再下载一次并按 `Content-Type` 决定扩展名。

`type` 含 `partial_image` 的中间帧会被跳过，只保存最终图。

### 输出命名

- 单图：按 `--out` 原样保存（目录则补默认文件名）；
- 多图（`--n 2` 等）：`name-1.png`、`name-2.png`；目录输入同理。

### 备用地址切换

只有 `network_error`（DNS/连接失败）和 `request_timeout` 才会用 `--fallback-base-url` 重试一次。HTTP 4xx/5xx、鉴权失败、参数错误、内容审核失败都在主地址上直接报错，避免掩盖真实原因。若两次都失败，错误详情里同时给出 `primary` 与 `fallback`。

## 输出格式

成功（stdout，退出码 0）：

```json
{
  "ok": true,
  "mode": "image-prompt",
  "model": "gpt-image-1",
  "paths": ["/abs/path/result.png"],
  "baseUrl": "https://api.example.com"
}
```

失败（stderr，退出码 1）：

```json
{
  "ok": false,
  "code": "api_http_error",
  "message": "生图接口返回非成功状态码",
  "detail": { "url": "https://api.example.com/v1/images/generations", "status": 401, "body": "…" }
}
```

### 错误码

| 错误码 | 含义 | 处理建议 |
| --- | --- | --- |
| `missing_api_key` | 没有任何密钥来源 | 设置 `IMAGE_GEN_API_KEY` 或传 `--api-key` |
| `missing_argument` | 缺 `--prompt` 或 `--image` | 检查模式的必填项 |
| `invalid_argument` | 参数格式错误 | 看 `detail` 里的 `key`/`value` |
| `invalid_mode` | `--mode` 不在三种模式内 | 用 `text`/`image`/`image-prompt` |
| `image_path_not_found` | 本地图片读不到 | 确认路径与读权限 |
| `image_url_fetch_failed` / `image_url_http_error` / `image_url_not_image` | 远程图片拉取失败或不是图片 | 换直链，或先下载到本地 |
| `image_data_url_not_image` / `invalid_image_data_url` | data URL 非法 | 必须是 `data:image/...;base64,...` |
| `env_file_not_found` / `env_file_unreadable` | 显式指定的 `.env` 有问题 | 检查路径与权限 |
| `api_http_error` | 服务端返回非 2xx | 看 `detail.body`，多为密钥/配额/参数问题 |
| `api_stream_error` | 流内出现 error 事件 | 常见为审核失败或模型不可用 |
| `sse_json_parse_error` / `json_parse_error` / `stream_unreadable` | 响应无法解析 | 确认端点返回的是图片或 SSE |
| `request_timeout` / `network_error` | 超时或网络不可达 | 增大 `--timeout`，或配置 `--fallback-base-url` |
| `no_image_result` | 流结束仍没有图片数据 | 服务端可能只返回了文本，检查 `--model` |
| `unexpected_error` | 未分类异常 | 把 `message` 与命令一起反馈 |

## 在不同客户端里使用

脚本不读取任何客户端私有目录，只依赖进程环境。

- **Codex / Claude Code**：安装到技能目录后直接按 `SKILL.md` 工作；密钥用 `IMAGE_GEN_API_KEY` 或已有 `OPENAI_API_KEY`。
- **支持 provider 配置的客户端**：把 base URL 与密钥写进其环境变量设置即可，键名与上表一致。
- **CI**：推荐密钥走环境变量注入，不落盘；需要固定服务端时把 `IMAGE_GEN_BASE_URL` 写进 CI 变量而非 `.env`。
- **Windows**：cmd 用 `set IMAGE_GEN_BASE_URL=...`（仅当前窗口），PowerShell 用 `$env:IMAGE_GEN_BASE_URL = "..."`；`.env` 位置见上文。

## 验证与测试

```bash
python3 tests/test_image_gen.py
```

测试会在 `127.0.0.1` 随机端口起一个 mock 图片服务，覆盖：JSON 与 SSE 两种返回、文生图与 multipart 图生图、`--image-field`、`--param`、环境变量与 `.env` 配置、错误码路径、帮助文本，并断言仓库中不存在原服务商的硬编码域名与模型预设。不需要网络，也不会发送任何密钥。

手工冒烟：

```bash
node scripts/node/image-gen.js --help && python3 scripts/python/image_gen.py --help
node --check scripts/node/image-gen.js && python3 -m py_compile scripts/python/image_gen.py
```

## 与原始技能包的差异

原始版本来自某个第三方网关的托管技能包，行为与文档都围绕它自己的服务。本仓库的改动：

| 项目 | 原版 | 现在 |
| --- | --- | --- |
| 默认 `--base-url` | 固定为某网关域名 | `IMAGE_GEN_BASE_URL`/`OPENAI_BASE_URL`，兜底 `https://api.openai.com/v1` |
| 默认 `--model` | 网关私有模型名（另有推荐模型清单） | `IMAGE_GEN_MODEL`/`OPENAI_MODEL`，兜底 `gpt-image-1` |
| 默认备用地址 | 内置该网关的镜像域名 | 默认不启用，按需 `--fallback-base-url` |
| 流式 | 强制 `stream: true` + `Accept: text/event-stream` | 默认关闭，`--stream true` 可开；SSE 返回仍能自动识别 |
| `quality` | 恒为 `auto` | 不显式指定就不提交，兼容严格校验的服务端 |
| `response_format` | 恒为 `b64_json` | 可省略，交给服务端；`b64_json`/`url` 都能解析 |
| 参数扩展 | 无 | 新增 `--param`、`--header`、`--image-field`、`--response-format` |
| 密钥文件 | 指向该网关的用户配置目录与密钥页面 | 改为通用 `~/.config/image-gen/.env` 等位置，不再外链引导 |
| 文档 | 面向单一网关 | 通用文档 + 服务端差异手册 |

## 已知限制

- 不做并发多任务；一次调用产出一批图片。
- 不解析 `--out` 里的 `~`，跨平台请使用绝对路径或让 Shell 展开。
- 只覆盖 OpenAI 兼容的图片端点；纯异步任务型接口（先提交再轮询）不在范围内。
- 远程图片 URL 下载复用 `--timeout`，没有独立超时项。
- Node 版需要 Node.js 18+（`fetch`/`FormData`/`Blob` 为内置）；Python 版需要 3.9+。
