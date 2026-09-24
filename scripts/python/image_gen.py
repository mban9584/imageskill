#!/usr/bin/env python3
"""通用 OpenAI 兼容图片接口脚本：文生图、图生图、文+图生图。

只依赖 Python 3 标准库，不绑定任何服务商；所有默认值都可以被环境变量或命令行覆盖。
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def first_string(*values):
    """返回第一个非空字符串，用于实现 env -> 默认值 的优先级链。"""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# 默认值在运行时读取：命令行 > 进程环境变量 > .env > 内置默认。
ENV_KEYS = ("IMAGE_GEN_BASE_URL", "IMAGE_GEN_MODEL", "IMAGE_GEN_SIZE", "IMAGE_GEN_QUALITY", "IMAGE_GEN_RESPONSE_FORMAT",
            "IMAGE_GEN_STREAM", "IMAGE_GEN_FALLBACK_BASE_URL", "IMAGE_GEN_IMAGE_FIELD", "IMAGE_GEN_PROMPT",
            "IMAGE_GEN_ENV_FILE", "OPENAI_BASE_URL")


def setting(primary, secondary, fallback):
    return first_string(os.environ.get(primary), os.environ.get(secondary) if secondary else "", fallback)


def default_base_url():
    return setting("IMAGE_GEN_BASE_URL", "OPENAI_BASE_URL", "https://api.openai.com/v1")


def default_model():
    return setting("IMAGE_GEN_MODEL", "OPENAI_MODEL", "gpt-image-1")


def default_fallback_base_url():
    return setting("IMAGE_GEN_FALLBACK_BASE_URL", "", "")


def default_size():
    return setting("IMAGE_GEN_SIZE", "", "1024x1024")


def default_quality():
    return setting("IMAGE_GEN_QUALITY", "", "")


def default_response_format():
    return setting("IMAGE_GEN_RESPONSE_FORMAT", "", "")


def default_stream():
    return setting("IMAGE_GEN_STREAM", "", "false")


def default_image_field():
    return setting("IMAGE_GEN_IMAGE_FIELD", "", "image")


def default_image_prompt():
    return setting("IMAGE_GEN_PROMPT", "", "根据参考图片生成一张新图片，保留主体与主要构图。")

GENERATIONS_PATH = "/v1/images/generations"
EDITS_PATH = "/v1/images/edits"
B64_KEYS = ("b64_json", "base64", "image_base64", "partial_image_b64")
URL_KEYS = ("url", "image_url", "result_url")


class UserError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def usage():
    return "\n".join([
        "用法：python3 scripts/python/image_gen.py --mode text|image|image-prompt --prompt <提示词> [参数]",
        "",
        "模式：",
        "  text                  文生图：只传提示词，调用 /v1/images/generations",
        "  image                 图生图：传一张或多张图片，提示词可省略",
        "  image-prompt          文+图生图：传一张或多张图片和提示词",
        "  edit                  image-prompt 的旧兼容别名",
        "",
        "通用参数：",
        "  --api-key <key>       优先级最高；其次 IMAGE_GEN_API_KEY / OPENAI_API_KEY / .env",
        "  --env-file <path>     指定 dotenv 文件，默认查找当前目录 .env 和用户配置文件",
        "  --base-url <url>      API 根地址，默认 IMAGE_GEN_BASE_URL/OPENAI_BASE_URL，否则 https://api.openai.com/v1",
        "  --fallback-base-url   主地址网络不可达或超时时的备用地址，默认不启用",
        "  --model <model>       图片模型名，默认 IMAGE_GEN_MODEL/OPENAI_MODEL，否则 gpt-image-1",
        "  --prompt <text>       text 和 image-prompt 必填；image 省略时使用默认提示词",
        "  --image <path|url>    本地路径、远程 URL 或 data:image Base64，可重复传入",
        "  --images <a,b,...>    逗号分隔的图片路径、URL 或 data URL，可重复传入",
        "  --image-field <name>  multipart 图片字段名，默认 image，也可用 image[]",
        "  --n <number>          生成数量，默认 1",
        "  --out <path|dir>      保存位置，默认 generated-image.png",
        "  --size <size>         默认 1024x1024",
        "  --quality <quality>   仅在显式传入时提交，避免被严格校验的服务端拒绝",
        "  --response-format     仅在显式传入时提交，可选 b64_json 或 url",
        "  --stream <true|false> 是否请求流式 SSE，默认 false；服务端流式返回也会被自动识别",
        "  --header <Name:value> 追加自定义请求头，可重复传入",
        "  --param <key=value>   追加任意请求体参数，可重复传入",
        "  --timeout <seconds>   默认 900",
        "",
        "示例：",
        "  python3 scripts/python/image_gen.py --prompt '一只戴宇航头盔的猫' --out ./cat.png",
        "  python3 scripts/python/image_gen.py --base-url http://127.0.0.1:8000/v1 --model sd3.5 --prompt '城市夜景'",
        "  python3 scripts/python/image_gen.py --mode image-prompt --image ./a.png --prompt '改成赛博朋克风格'",
    ])


def normalize_mode(mode):
    value = str(mode or "text").lower()
    aliases = {
        "image-to-image": "image",
        "image_to_image": "image",
        "text-to-image": "text",
        "text-image": "image-prompt",
        "text_image": "image-prompt",
        "t2i": "text",
        "i2i": "image",
        "vision": "image-prompt",
        "edit": "image-prompt",
    }
    normalized = aliases.get(value, value)
    if normalized not in ("text", "image", "image-prompt"):
        raise UserError("invalid_mode", "--mode 只能是 text、image 或 image-prompt", {"mode": mode})
    return normalized


def parse_positive_int(value, key):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise UserError("invalid_argument", f"--{key} 必须是正整数", {"key": key, "value": value}) from error
    if parsed < 1:
        raise UserError("invalid_argument", f"--{key} 必须是正整数", {"key": key, "value": value})
    return parsed


def parse_bool(value, key, fallback):
    if value is None:
        value = fallback
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    raise UserError("invalid_argument", f"--{key} 只能是 true 或 false", {"key": key, "value": value})


def parse_extra_param(item):
    """--param key=value：值支持 JSON、布尔与数字，其余按字符串提交。"""
    equal_at = item.find("=")
    if equal_at < 1:
        raise UserError("invalid_argument", "--param 需要写成 key=value", {"value": item})
    key = item[:equal_at].strip()
    raw = item[equal_at + 1:].strip()
    value = raw
    if raw[:1] in ("{", "[", '"'):
        try:
            value = json.loads(raw)
        except ValueError as error:
            raise UserError("invalid_argument", f"--param {key} 的值不是合法 JSON", {"key": key, "value": raw}) from error
    elif raw in ("true", "false"):
        value = raw == "true"
    elif raw != "":
        try:
            value = float(raw) if re.search(r"[.eE]", raw) else int(raw)
        except ValueError:
            value = raw
    return key, value


def parse_extra_header(item):
    colon_at = item.find(":")
    if colon_at < 1:
        raise UserError("invalid_argument", '--header 需要写成 "Name: value"', {"value": item})
    return item[:colon_at].strip(), item[colon_at + 1:].strip()


def flatten(values):
    result = []
    for item in values or []:
        for part in str(item).split(","):
            if part.strip():
                result.append(part.strip())
    return result


def parse_args(argv=None):
    # 手写解析，避免 argparse 把未知参数报错出去，保持与 Node 版本一致的 --key=value 风格。
    args = {
        "mode": "text", "api-key": "", "env-file": "", "base-url": "", "fallback-base-url": "",
        "model": "", "prompt": "", "size": "", "quality": None, "response-format": None,
        "image-field": "", "n": "1", "out": "", "timeout": "900", "stream": None,
    }
    lists = {"image": [], "images": [], "header": [], "param": []}
    unknown = []
    argv = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(argv):
        item = argv[index]
        if not item.startswith("--"):
            unknown.append(item)
            index += 1
            continue
        if "=" in item[2:]:
            key, _, value = item[2:].partition("=")
            index += 1
        else:
            key = item[2:]
            nxt = argv[index + 1] if index + 1 < len(argv) else None
            if nxt is None or nxt.startswith("--"):
                value, index = "true", index + 1
            else:
                value, index = nxt, index + 2
        if key in ("help", "h"):
            args["help"] = "true"
            continue
        if key in lists:
            lists[key].append(value)
            continue
        if key in args:
            args[key] = value
        else:
            unknown.append(item)
    if unknown:
        raise UserError("invalid_argument", "无法识别的参数：" + " ".join(unknown), {"arguments": unknown})

    args["help"] = parse_bool(args.get("help", "false"), "help", False)
    args["mode"] = normalize_mode(args["mode"])
    args["n"] = parse_positive_int(args["n"], "n")
    args["timeout"] = parse_positive_int(args["timeout"], "timeout")
    args["env_file"] = first_string(args["env-file"], os.environ.get("IMAGE_GEN_ENV_FILE", ""))
    args["images_list"] = flatten(lists["image"]) + flatten(lists["images"])
    args["headers"] = dict(parse_extra_header(item) for item in lists["header"])
    args["params"] = dict(parse_extra_param(item) for item in lists["param"])
    return args


def build_api_url(base_url, api_path):
    base = (base_url or default_base_url()).rstrip("/")
    path_with_v1 = api_path if api_path.startswith("/") else f"/{api_path}"
    if base.endswith("/v1") and path_with_v1.startswith("/v1/"):
        return f"{base}{path_with_v1[3:]}"
    return f"{base}{path_with_v1}"


def content_type_from_path(file_path):
    guessed = mimetypes.guess_type(str(file_path))[0]
    if guessed and guessed.startswith("image/"):
        return guessed
    return "image/png"


def extension_from_content_type(content_type):
    subtype = str(content_type or "").split("/")[1:2]
    clean = (subtype[0] if subtype else "").split("+")[0].split(";")[0].strip()
    if clean in ("jpeg", "jpg"):
        return "jpg"
    return clean or "png"


def parse_dotenv(text):
    values = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def user_env_paths():
    home = Path.home()
    if os.name == "nt":
        app_data = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return [Path(app_data) / "image-gen" / ".env", home / ".env"]
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    return [Path(config_home) / "image-gen" / ".env", home / ".env"]


def load_dotenv(args):
    explicit = args["env_file"]
    candidates = [Path(explicit)] if explicit else [Path.cwd() / ".env", *user_env_paths()]
    values = {}
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            # 越靠前的文件优先级越高，后面的文件不覆盖已经读到的键。
            for key, value in parse_dotenv(candidate.read_text(encoding="utf-8")).items():
                values.setdefault(key, value)
        except FileNotFoundError:
            if explicit:
                raise UserError("env_file_not_found", "指定的 .env 文件不存在", {"envFile": str(candidate)})
        except OSError:
            if explicit:
                raise UserError("env_file_unreadable", "指定的 .env 文件无法读取", {"envFile": str(candidate)})
    for key in ENV_KEYS:
        if values.get(key) and not os.environ.get(key):
            os.environ[key] = values[key]
    return values


def resolve_settings(args):
    """在 .env 合并进环境变量之后，再解析所有默认值。"""
    args["stream"] = parse_bool(args["stream"], "stream", default_stream())
    args["quality"] = first_string(args["quality"], default_quality())
    args["response_format"] = first_string(args["response-format"], default_response_format())
    args["base_url"] = first_string(args["base-url"], default_base_url())
    args["fallback_base_url"] = first_string(args["fallback-base-url"], default_fallback_base_url())
    args["image_field"] = first_string(args["image-field"], default_image_field())


def resolve_api_key(args):
    env_file = load_dotenv(args)
    api_key = first_string(args["api-key"], os.environ.get("IMAGE_GEN_API_KEY"), os.environ.get("OPENAI_API_KEY"),
                           env_file.get("IMAGE_GEN_API_KEY"), env_file.get("OPENAI_API_KEY"))
    if not api_key:
        raise UserError(
            "missing_api_key",
            "缺少 API Key。请用 --api-key 传入，或设置 IMAGE_GEN_API_KEY / OPENAI_API_KEY 环境变量，或在 .env 中写一行 IMAGE_GEN_API_KEY=你的密钥。",
            {"key": "IMAGE_GEN_API_KEY", "envVars": ["IMAGE_GEN_API_KEY", "OPENAI_API_KEY"]},
        )
    return api_key


def open_url(url, options, timeout):
    request = urllib.request.Request(url, data=options.get("body"), method=options.get("method", "POST"),
                                     headers=options.get("headers") or {})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else ""
        raise UserError("api_http_error", "生图接口返回非成功状态码", {"url": url, "status": error.code, "body": body}) from error
    except urllib.error.URLError as error:
        reason = str(getattr(error, "reason", error))
        code = "request_timeout" if "timed out" in reason.lower() else "network_error"
        raise UserError(code, "无法连接生图接口", {"url": url, "reason": reason}) from error
    except (TimeoutError, socket.timeout) as error:
        raise UserError("request_timeout", "生图接口请求超时", {"url": url, "timeout": timeout}) from error


def request_with_fallback(request, args):
    primary_url = request["url"]
    try:
        return open_url(primary_url, request["options"], request["timeout"]), primary_url
    except UserError as error:
        if error.code not in ("network_error", "request_timeout"):
            raise
        fallback_base = first_string(args.get("fallback_base_url"), "")
        if not fallback_base:
            raise
        fallback_url = build_api_url(fallback_base, request["path"])
        if fallback_url == primary_url:
            raise
        try:
            return open_url(fallback_url, request["options"], request["timeout"]), fallback_url
        except UserError as fallback_error:
            raise UserError(error.code, f"{error}；备用地址也无法连接", {
                "primary": error.detail,
                "fallback": fallback_error.detail or {"url": fallback_url},
            }) from fallback_error


def read_url(url, timeout, expect_image=False):
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "*/*"}), timeout=timeout)
    except urllib.error.HTTPError as error:
        raise UserError("image_url_http_error", "图片链接返回非成功状态码", {"image": url, "status": error.code}) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UserError("image_url_fetch_failed", "图片链接无法打开", {"image": url, "reason": str(error)}) from error
    with response:
        content_type = (response.headers.get("content-type") or "application/octet-stream").split(";")[0]
        if expect_image and not content_type.startswith("image/"):
            raise UserError("image_url_not_image", "图片链接返回的内容不是图片", {"image": url, "contentType": content_type})
        return response.read(), content_type


def fetch_image_url(url, timeout):
    data, content_type = read_url(url, timeout, expect_image=True)
    name = f"source.{extension_from_content_type(content_type)}"
    tail = Path(urllib.parse.urlparse(url).path).name
    return data, content_type, tail or name


def data_url_to_file(value):
    match = re.match(r"^data:([^;,]+)?;base64,(.+)$", value.strip(), re.IGNORECASE | re.DOTALL)
    if not match:
        raise UserError("invalid_image_data_url", "图片 data URL 无效，必须使用 data:image/...;base64,... 格式")
    content_type = match.group(1) or "image/png"
    if not content_type.lower().startswith("image/"):
        raise UserError("image_data_url_not_image", "图片 data URL 的 MIME 类型不是 image/*")
    try:
        data = base64.b64decode(match.group(2), validate=False)
    except (ValueError, base64.binascii.Error) as error:
        raise UserError("invalid_image_data_url", "图片 data URL 的 Base64 无法解码") from error
    return data, content_type, f"source.{extension_from_content_type(content_type)}"


def image_to_multipart_file(image, timeout):
    if re.match(r"^https?://", image, re.IGNORECASE):
        return fetch_image_url(image, timeout)
    if image.lower().startswith("data:"):
        return data_url_to_file(image)
    path = Path(image)
    try:
        return path.read_bytes(), content_type_from_path(path), path.name or "source.png"
    except OSError as error:
        raise UserError("image_path_not_found", "本地图片路径不存在或不可读取", {"image": image}) from error


def encode_multipart(fields, files, field_name):
    boundary = "----imagegen-" + uuid.uuid4().hex
    payload = bytearray()
    for key, value in fields.items():
        if value is None or value == "":
            continue
        payload.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for data, content_type, name in files:
        payload.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{name}"\r\n'
                       f"Content-Type: {content_type}\r\n\r\n".encode())
        payload.extend(data)
        payload.extend(b"\r\n")
    payload.extend(f"--{boundary}--\r\n".encode())
    return bytes(payload), "multipart/form-data; boundary=" + boundary


def request_headers(api_key, stream, extra, accept=None):
    headers = {"Authorization": f"Bearer {api_key}", "Accept": accept or ("text/event-stream" if stream else "application/json")}
    headers.update(extra or {})
    return headers


def is_partial_image_event(event):
    return event.get("type") in ("image_generation.partial_image", "response.image_generation_call.partial_image")


def throw_if_error_event(event):
    if not isinstance(event, dict):
        return
    failed = event.get("type") in ("error", "response.failed", "failed")
    error = event.get("error") if isinstance(event.get("error"), dict) else None
    if not failed and not error:
        return
    message = ((event.get("response") or {}).get("error") or {}).get("message") or (error or {}).get("message") or event.get("message") or ""
    if not failed and not message:
        return
    raise UserError("api_stream_error", "生图接口返回错误", {"type": event.get("type"), "message": message})


def as_list(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, (dict, list))]
    if isinstance(value, dict):
        return [value]
    return []


def collect_refs(event, refs=None):
    """递归收集事件里的图片结果：Base64 字符串或图片 URL。"""
    if refs is None:
        refs = []
    if isinstance(event, list):
        for item in event:
            collect_refs(item, refs)
        return refs
    if not isinstance(event, dict):
        return refs
    for key in B64_KEYS:
        value = event.get(key)
        if isinstance(value, str) and value:
            refs.append({"kind": "b64", "value": value})
    for key in URL_KEYS:
        value = event.get(key)
        if isinstance(value, str) and re.match(r"^https?://", value, re.IGNORECASE):
            refs.append({"kind": "url", "value": value})
    for key in ("data", "images", "output", "results", "artifacts"):
        for item in as_list(event.get(key)):
            collect_refs(item, refs)
    for item in as_list((event.get("response") or {}).get("output") if isinstance(event.get("response"), dict) else None):
        collect_refs(item, refs)
    if isinstance(event.get("item"), dict):
        collect_refs(event["item"], refs)
    return refs


def iter_events(response):
    """按 SSE 帧或整体 JSON 产出事件，两种返回形态都能解析。"""
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" not in content_type:
        body = response.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            yield from parsed if isinstance(parsed, list) else [parsed]
            return
        except ValueError:
            if not body.strip():
                return
            yield from parse_sse(body)
        return
    buffer = ""
    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buffer += chunk.decode("utf-8", errors="replace")
        frames = re.split(r"\r?\n\r?\n", buffer)
        buffer = frames.pop()
        yield from parse_sse("\n\n".join(frames))
    yield from parse_sse(buffer)


def parse_sse(text):
    for frame in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")).strip()
        if not data or data.lower() == "[done]":
            continue
        try:
            event = json.loads(data)
        except ValueError as error:
            raise UserError("sse_json_parse_error", "SSE data 不是合法 JSON", {"data": data, "reason": str(error)}) from error
        if event:
            yield event if isinstance(event, dict) else {"data": event}


def output_path_for(out, index, total, default_name):
    target = Path(out or default_name)
    is_directory = str(out or "").endswith(("/", "\\")) or target.suffix == ""
    if is_directory:
        default = Path(default_name)
        name = default.name if total == 1 else f"{default.stem}-{index + 1}{default.suffix}"
        return target / name
    if total == 1:
        return target
    return target.with_name(f"{target.stem}-{index + 1}{target.suffix}")


def save_refs(refs, out, default_name, timeout):
    paths = []
    for index, ref in enumerate(refs):
        if ref["kind"] == "b64":
            try:
                data = base64.b64decode(ref["value"], validate=False)
            except (ValueError, base64.binascii.Error) as error:
                raise UserError("invalid_image_base64", "接口返回的 Base64 图片无法解码", {"index": index}) from error
            fallback_name = default_name
        else:
            data, content_type = read_url(ref["value"], timeout)
            fallback_name = f"generated-image.{extension_from_content_type(content_type)}"
        file_path = output_path_for(out, index, len(refs), fallback_name).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        paths.append(str(file_path))
    return paths


def build_request(args, api_key):
    mode = args["mode"]
    timeout = args["timeout"]
    base_url = args["base_url"]
    model = first_string(args["model"], default_model())
    stream = args["stream"]
    size = first_string(args["size"], default_size())

    # 只提交必要参数：不同服务端对未知/不兼容参数的容忍度差别很大。
    fields = {"model": model, "n": args["n"], "size": size}
    fields.update(args["params"])
    if args["quality"]:
        fields["quality"] = args["quality"]
    if args["response_format"]:
        fields["response_format"] = args["response_format"]
    if stream:
        fields["stream"] = True

    if mode == "text":
        if not args["prompt"]:
            raise UserError("missing_argument", "text 模式必须传入 --prompt", {"key": "prompt"})
        url = build_api_url(base_url, GENERATIONS_PATH)
        body = dict(fields, prompt=args["prompt"])
        options = {
            "method": "POST",
            "headers": request_headers(api_key, stream, dict(args["headers"], **{"Content-Type": "application/json"})),
            "body": json.dumps(body, ensure_ascii=False).encode(),
        }
        return {"mode": mode, "path": GENERATIONS_PATH, "url": url, "timeout": timeout, "model": model,
                "default_name": "generated-image.png", "options": options}

    images = args["images_list"]
    if not images:
        raise UserError("missing_argument", f"{mode} 模式至少传入一个 --image", {"key": "image", "mode": mode})
    if mode == "image-prompt" and not args["prompt"]:
        raise UserError("missing_argument", "image-prompt 模式必须传入 --prompt", {"key": "prompt"})
    files = [image_to_multipart_file(image, timeout) for image in images]
    body = dict(fields, prompt=args["prompt"] or default_image_prompt())
    form, content_type = encode_multipart(body, files, args["image_field"])
    url = build_api_url(base_url, EDITS_PATH)
    options = {
        "method": "POST",
        "headers": dict(request_headers(api_key, stream, args["headers"], accept="*/*"), **{"Content-Type": content_type}),
        "body": form,
    }
    return {"mode": mode, "path": EDITS_PATH, "url": url, "timeout": timeout, "model": model,
            "default_name": "generated-image.png", "options": options}


def main():
    args = parse_args()
    if args.get("help"):
        print(usage())
        return
    api_key = resolve_api_key(args)
    resolve_settings(args)
    request = build_request(args, api_key)
    response, actual_url = request_with_fallback(request, args)
    with response:
        for event in iter_events(response):
            throw_if_error_event(event)
            if is_partial_image_event(event):
                continue
            refs = collect_refs(event)
            if refs:
                paths = save_refs(refs[:args["n"]], args["out"], request["default_name"], request["timeout"])
                parsed = urllib.parse.urlparse(actual_url)
                print(json.dumps({"ok": True, "mode": request["mode"], "model": request["model"], "paths": paths,
                                  "baseUrl": f"{parsed.scheme}://{parsed.netloc}"}, ensure_ascii=False, indent=2))
                return
    raise UserError("no_image_result", "响应结束前没有收到图片结果数据", {"mode": request["mode"], "url": actual_url})


if __name__ == "__main__":
    try:
        main()
    except UserError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error), "detail": error.detail},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:  # noqa: BLE001 - 统一输出 JSON 错误，避免堆栈污染调用方
        print(json.dumps({"ok": False, "code": "unexpected_error", "message": str(error), "detail": {}},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
