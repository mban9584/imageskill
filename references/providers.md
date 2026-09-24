# 各类服务端的接入差异

本技能不内置任何服务商预设。下面给出常见部署形态的接法与踩坑点；具体字段以服务端文档为准，冲突时用 `--param` / `--header` 覆盖脚本默认行为。

## OpenAI 官方图片接口

```bash
export OPENAI_API_KEY='sk-…'
node scripts/node/image-gen.js --model gpt-image-1 --size 1024x1024 --prompt "一只戴宇航头盔的猫" --out ./cat.png
```

- 默认基址就是 `https://api.openai.com/v1`，不用额外配置。
- 尺寸常见取值 `1024x1024`、`1536x1024`、`1024x1536`、`auto`；质量 `low|medium|high|auto`。
- 需要透明背景：`--param background=transparent --param output_format=png`。
- 参考图走 `edits`，多张图重复 `image` 字段。遮罩等文件型参数若服务端要求放在请求体里，用 `--param` 传 Base64 字符串，或按服务端要求改用 `--image` 多次传入。

## 兼容网关（one-api / new-api / 各类聚合站）

```bash
node scripts/node/image-gen.js --base-url https://<网关>/v1 --model <网关里的模型名> --prompt "海报" --out ./poster.png
```

- 模型名以网关控制台为准，脚本原样透传；报 `model_not_found` 通常是该密钥分组未开通模型，不是脚本问题。
- 有的网关只代理 `generations`，不代理 `edits`；图生图失败时改用一个接受 URL 的模型，或先本地转成 base64 再传 `--image data:image/png;base64,…`。
- 需要租户/项目头的，用 `--header 'X-Project: demo'`。

## 本机推理服务（vLLM、LM Studio、SD WebUI API 桥、ComfyUI 桥）

```bash
# 例：本机 OpenAI 兼容服务
IMAGE_GEN_BASE_URL=http://127.0.0.1:8000/v1 IMAGE_GEN_MODEL=sd3.5-large \
  python3 scripts/python/image_gen.py --prompt "城市夜景" --out ./night.png
```

- 本机服务多数不需要真实密钥，但仍要有一个非空值：`--api-key local` 即可（用 `IMAGE_GEN_API_KEY` 更省事）。
- 常见差异：`n` 不支持、`quality` 被拒绝、`size` 只接受固定档位、返回只有 `url` 且是 `http://127.0.0.1:port/...` 的临时链接。脚本会在同一进程内下载该链接，请保证服务在脚本结束前保持运行。
- 若服务端只接受 `image[]` 数组字段：`--image-field 'image[]'`。
- 若要关掉 `response_format`（部分实现不认识该字段）：不要传 `--response-format`，默认即不提交。

## 自建反代与内网服务

```bash
# 内网服务走代理
node scripts/node/image-gen.js --base-url https://img.internal/v1 --timeout 60 --prompt "内部测试"
```

- 需要额外鉴权头：`--header 'Authorization: Bearer <token>'` 会覆盖脚本默认的 `Authorization`；若想保留 Bearer 又加自定义头，用别的头名（如 `--header 'X-Auth: …'`）。
- 自签证书：Node 侧用 `NODE_EXTRA_CA_CERTS=/path/ca.pem`，Python 侧用 `SSL_CERT_FILE=/path/ca.pem`。
- 双活端点：`--base-url https://a.example.com/v1 --fallback-base-url https://b.example.com/v1`。

## 参数速查

| 场景 | 做法 |
| --- | --- |
| 换服务端 | `--base-url` 或 `IMAGE_GEN_BASE_URL` |
| 换模型 | `--model` 或 `IMAGE_GEN_MODEL` |
| 服务端拒绝未知字段 | 去掉 `--quality` / `--response-format`（默认即不提交） |
| 需要私有字段 | `--param key=value`，值为 JSON 时写 `--param 'extra={"a":1}'` |
| 需要非 Bearer 鉴权 | `--header 'api-key: xxx'`，并保留 `--api-key` 为任意非空值以通过本地检查 |
| 参考图字段不同 | `--image-field 'image[]'` |
| 首图慢 | 增大 `--timeout`，或开 `--stream true` 观察进度 |
| 只要外链不要本地文件 | 目前脚本总会落盘；把 `--out` 指到临时目录即可 |
