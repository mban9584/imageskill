# OpenAI 兼容图片接口配方

本文件是给“移植到其他语言/自行封装”的人看的。日常调用请直接用 `scripts/node/image-gen.js` 或 `scripts/python/image_gen.py`。

约定：`{BASE}` 指服务端根地址，形如 `https://api.example.com/v1`（也可不带 `/v1`，脚本会自动补齐）。

## 统一约定

- 文生图：`POST {BASE}/images/generations`，`Content-Type: application/json`。
- 图生图 / 文+图生图：`POST {BASE}/images/edits`，`Content-Type: multipart/form-data`。
- 鉴权：`Authorization: Bearer <API_KEY>`。部分网关改用 `api-key` 或自定义头，用 `--header` 覆盖。
- 最小请求体：`model`、`prompt`、`n`、`size`。其余字段（`quality`、`response_format`、`stream`、私有字段）按需追加，能不送就不送。
- 流式：请求体 `"stream": true` + `Accept: text/event-stream`；即使服务端忽略 `stream`，只要它返回 SSE，解析逻辑同样适用。
- 结果位置：`data[].b64_json`、`data[].url`、事件里的 `b64_json`、`item.result`。三种都要试。
- 中间帧：`type` 含 `partial_image` 的事件仅用于预览，最终图取完成事件。

## 文生图（非流式）

```bash
curl -sS "{BASE}/images/generations" \
  -H "Authorization: Bearer $IMAGE_GEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-1","prompt":"一只戴宇航头盔的猫","n":1,"size":"1024x1024"}' \
  | python3 -c 'import base64,json,sys; d=json.load(sys.stdin); open("out.png","wb").write(base64.b64decode(d["data"][0]["b64_json"]))'
```

```python
import base64, json, os, urllib.request

body = {"model": "gpt-image-1", "prompt": "一只戴宇航头盔的猫", "n": 1, "size": "1024x1024"}
request = urllib.request.Request(
    f"{os.environ['IMAGE_GEN_BASE_URL']}/images/generations",
    data=json.dumps(body).encode(),
    method="POST",
    headers={
        "Authorization": "Bearer " + os.environ["IMAGE_GEN_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=900) as response:
    item = json.load(response)["data"][0]
if item.get("b64_json"):
    image = base64.b64decode(item["b64_json"])
else:  # 只返回外链时再下载一次
    with urllib.request.urlopen(item["url"], timeout=900) as image_response:
        image = image_response.read()
open("generated-image.png", "wb").write(image)
```

## 文生图（流式 SSE）

SSE 帧以空行分隔，数据行以 `data:` 开头；`data: [DONE]` 表示结束。

```text
event: image_generation.partial_image
data: {"type":"image_generation.partial_image","b64_json":"…","index":0}

event: image_generation.completed
data: {"type":"image_generation.completed","b64_json":"…","index":0}

data: [DONE]
```

```python
import base64, json, os, urllib.request

body = {"model": "gpt-image-1", "prompt": "城市夜景", "n": 1, "size": "1024x1024", "stream": True}
request = urllib.request.Request(
    f"{os.environ['IMAGE_GEN_BASE_URL']}/images/generations",
    data=json.dumps(body).encode(),
    method="POST",
    headers={
        "Authorization": "Bearer " + os.environ["IMAGE_GEN_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
)

def frames(response):
    buffer = ""
    while chunk := response.read(4096).decode("utf-8", "replace"):
        buffer += chunk
        parts = buffer.split("\n\n")
        buffer = parts.pop()
        yield from parts

with urllib.request.urlopen(request, timeout=900) as response:
    for frame in frames(response):
        data = "\n".join(line[5:].strip() for line in frame.splitlines() if line.startswith("data:")).strip()
        if not data or data == "[DONE]":
            continue
        event = json.loads(data)
        if "partial_image" in str(event.get("type", "")):
            continue
        image = event.get("b64_json") or (event.get("data") or [{}])[0].get("b64_json")
        if image:
            open("generated-image.png", "wb").write(base64.b64decode(image))
            break
```

## 图生图 / 文+图生图

两种模式走同一个 `edits` 端点，区别只在是否带自己的 `prompt`。多图重复使用同一个字段名（默认 `image`，部分服务端要求 `image[]`）。

```bash
curl -sS "{BASE}/images/edits" \
  -H "Authorization: Bearer $IMAGE_GEN_API_KEY" \
  -F "model=gpt-image-1" \
  -F "prompt=改成赛博朋克风格，保留主体" \
  -F "size=1024x1024" \
  -F "n=1" \
  -F "image=@a.png" \
  -F "image=@b.png"
```

Python 侧手写 multipart（与脚本实现一致，便于无 `requests` 环境移植）：

```python
import base64, json, os, urllib.request, uuid
from pathlib import Path

boundary = "----imagegen-" + uuid.uuid4().hex
fields = {"model": "gpt-image-1", "prompt": "融合两张参考图", "n": "1", "size": "1024x1024"}
files = [("image", name, Path(name).read_bytes(), "image/png") for name in ("a.png", "b.png")]

payload = bytearray()
for key, value in fields.items():
    payload.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
for field, name, data, content_type in files:
    payload.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode())
    payload.extend(data)
    payload.extend(b"\r\n")
payload.extend(f"--{boundary}--\r\n".encode())

request = urllib.request.Request(
    f"{os.environ['IMAGE_GEN_BASE_URL']}/images/edits",
    data=bytes(payload),
    method="POST",
    headers={
        "Authorization": "Bearer " + os.environ["IMAGE_GEN_API_KEY"],
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Accept": "*/*",
    },
)
with urllib.request.urlopen(request, timeout=900) as response:
    payload = json.load(response)
open("generated-image.png", "wb").write(base64.b64decode(payload["data"][0]["b64_json"]))
```

## 响应形态速查

| 形态 | 示例 | 取值路径 |
| --- | --- | --- |
| 标准 JSON | `{"data":[{"b64_json":"…"}]}` | `data[0].b64_json` |
| 外链 JSON | `{"data":[{"url":"https://…"}]}` | 需要再下载一次 |
| 完成事件 | `{"type":"image_generation.completed","b64_json":"…"}` | 顶层 `b64_json` |
| Responses 风格 | `{"type":"response.output_item.added","item":{"type":"image_generation_call","result":"…"}}` | `item.result` |
| 数组输出 | `{"output":[{"type":"image","image_url":{"url":"…"}}]}` | 递归查找 `url`/`b64_json` |
| 错误事件 | `{"type":"error","error":{"message":"…"}}` | 立即中止并报错 |

脚本的递归查找顺序：先看当前对象的 Base64 键（`b64_json`、`base64`、`image_base64`、`partial_image_b64`），再看 URL 键（`url`、`image_url`、`result_url`），最后下钻 `data`、`images`、`output`、`results`、`artifacts`、`item`、`response.output`。

## 移植清单

1. 两种模式两个端点，别把图片当 JSON 字段发。
2. 只送服务端认识的字段，未知字段是 400 的头号原因。
3. 同时支持“整体 JSON”和“逐帧 SSE”；用 `Content-Type` 判断，判断不了就先试 JSON、失败再按 SSE 拆帧。
4. 兼容 `b64_json` 与 `url` 两种结果；`url` 需要二次下载并保留真实扩展名。
5. 跳过 `partial_image`，只保存完成结果。
6. 输出路径要能处理目录、多图编号、以及服务端返回的格式差异。
7. 网络失败可以换备用地址；HTTP 状态错误不要换，直接报告原文。
8. 任何日志与错误信息里都不出现密钥。
