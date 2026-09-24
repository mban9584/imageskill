#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-var-requires */
const os = require("node:os");
const { mkdir, readFile, writeFile } = require("node:fs/promises");
const path = require("node:path");

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

// 所有预设都可以被环境变量或命令行覆盖，脚本本身不绑定任何服务商。
// 默认值在运行时读取：命令行 > 进程环境变量 > .env > 内置默认。
function setting(primary, secondary, fallback) {
  return firstString(process.env[primary], secondary ? process.env[secondary] : "", fallback);
}

const defaultBaseUrl = () => setting("IMAGE_GEN_BASE_URL", "OPENAI_BASE_URL", "https://api.openai.com/v1");
const defaultModel = () => setting("IMAGE_GEN_MODEL", "OPENAI_MODEL", "gpt-image-1");
const defaultFallbackBaseUrl = () => setting("IMAGE_GEN_FALLBACK_BASE_URL", "", "");
const defaultSize = () => setting("IMAGE_GEN_SIZE", "", "1024x1024");
const defaultQuality = () => setting("IMAGE_GEN_QUALITY", "", "");
const defaultResponseFormat = () => setting("IMAGE_GEN_RESPONSE_FORMAT", "", "");
const defaultStream = () => setting("IMAGE_GEN_STREAM", "", "false");
const defaultImageField = () => setting("IMAGE_GEN_IMAGE_FIELD", "", "image");
const defaultImagePrompt = () => setting("IMAGE_GEN_PROMPT", "", "根据参考图片生成一张新图片，保留主体与主要构图。");

const GENERATIONS_PATH = "/v1/images/generations";
const EDITS_PATH = "/v1/images/edits";
const B64_KEYS = ["b64_json", "base64", "image_base64", "partial_image_b64"];
const URL_KEYS = ["url", "image_url", "result_url"];

class UserError extends Error {
  constructor(code, message, detail = {}) {
    super(message);
    this.code = code;
    this.detail = detail;
  }
}

function addArg(args, key, value) {
  if (args[key] === undefined) args[key] = value;
  else if (Array.isArray(args[key])) args[key].push(value);
  else args[key] = [args[key], value];
}

function parseArgs(argv = process.argv.slice(2)) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const equalAt = item.indexOf("=");
    if (equalAt > 2) {
      addArg(args, item.slice(2, equalAt), item.slice(equalAt + 1));
      continue;
    }
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) addArg(args, key, "true");
    else {
      addArg(args, key, next);
      i += 1;
    }
  }
  return args;
}

function firstArg(args, key, fallback = "") {
  const value = args[key];
  return firstString(Array.isArray(value) ? value[0] : value, fallback);
}

function listArg(args, key) {
  const value = args[key];
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value])
    .flatMap((item) => String(item).split(","))
    .map((item) => item.trim())
    .filter(Boolean);
}

function truthy(value) {
  return ["1", "true", "yes", "y", "on"].includes(String(value).toLowerCase());
}

function boolArg(args, key, fallback) {
  if (args[key] === undefined) return truthy(fallback);
  const value = String(firstArg(args, key, String(fallback))).toLowerCase();
  if (["1", "true", "yes", "y", "on"].includes(value)) return true;
  if (["0", "false", "no", "n", "off"].includes(value)) return false;
  throw new UserError("invalid_argument", `--${key} 只能是 true 或 false`, { key, value });
}

function intArg(args, key, fallback) {
  const raw = firstArg(args, key, String(fallback));
  const value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value) || value < 1) {
    throw new UserError("invalid_argument", `--${key} 必须是正整数`, { key, value: raw });
  }
  return value;
}

function extraParams(args) {
  const params = {};
  for (const item of listArg(args, "param")) {
    const equalAt = item.indexOf("=");
    if (equalAt < 1) throw new UserError("invalid_argument", "--param 需要写成 key=value", { value: item });
    const key = item.slice(0, equalAt).trim();
    const raw = item.slice(equalAt + 1).trim();
    let value = raw;
    if (raw.startsWith("{") || raw.startsWith("[") || raw.startsWith('"')) {
      try {
        value = JSON.parse(raw);
      } catch {
        throw new UserError("invalid_argument", `--param ${key} 的值不是合法 JSON`, { key, value: raw });
      }
    } else if (raw === "true" || raw === "false") {
      value = raw === "true";
    } else if (raw !== "" && Number.isFinite(Number(raw))) {
      value = Number(raw);
    }
    params[key] = value;
  }
  return params;
}

function extraHeaders(args) {
  const headers = {};
  for (const item of listArg(args, "header")) {
    const colonAt = item.indexOf(":");
    if (colonAt < 1) throw new UserError("invalid_argument", '--header 需要写成 "Name: value"', { value: item });
    headers[item.slice(0, colonAt).trim()] = item.slice(colonAt + 1).trim();
  }
  return headers;
}

function usage() {
  return [
    "用法：node scripts/node/image-gen.js --mode text|image|image-prompt --prompt <提示词> [参数]",
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
    "  node scripts/node/image-gen.js --prompt '一只戴宇航头盔的猫' --out ./cat.png",
    "  node scripts/node/image-gen.js --base-url http://127.0.0.1:8000/v1 --model sd3.5 --prompt '城市夜景'",
    "  node scripts/node/image-gen.js --mode image-prompt --image ./a.png --prompt '改成赛博朋克风格'",
  ].join("\n");
}

function normalizeMode(mode) {
  const value = String(mode || "text").toLowerCase();
  const aliases = {
    "image-to-image": "image",
    "image_to_image": "image",
    "text-to-image": "text",
    "text-image": "image-prompt",
    "text_image": "image-prompt",
    t2i: "text",
    i2i: "image",
    vision: "image-prompt",
    edit: "image-prompt",
  };
  const normalized = aliases[value] || value;
  if (!["text", "image", "image-prompt"].includes(normalized)) {
    throw new UserError("invalid_mode", "--mode 只能是 text、image 或 image-prompt", { mode });
  }
  return normalized;
}

function buildApiUrl(baseUrl, pathWithV1) {
  const base = (baseUrl || defaultBaseUrl()).replace(/\/+$/, "");
  const apiPath = pathWithV1.startsWith("/") ? pathWithV1 : `/${pathWithV1}`;
  if (base.endsWith("/v1") && apiPath.startsWith("/v1/")) return `${base}${apiPath.slice(3)}`;
  return `${base}${apiPath}`;
}

function contentTypeFromPath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  return "image/png";
}

function extensionFromContentType(contentType) {
  const subtype = String(contentType || "").split("/")[1] || "";
  const clean = subtype.split("+")[0].split(";")[0].trim();
  if (clean === "jpeg" || clean === "jpg") return "jpg";
  return clean || "png";
}

function parseDotEnv(text) {
  const values = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

function userEnvPaths() {
  const home = os.homedir();
  if (process.platform === "win32") {
    const appData = process.env.APPDATA || path.join(home, "AppData", "Roaming");
    return [path.join(appData, "image-gen", ".env"), path.join(home, ".env")];
  }
  const configHome = process.env.XDG_CONFIG_HOME || path.join(home, ".config");
  return [path.join(configHome, "image-gen", ".env"), path.join(home, ".env")];
}

const ENV_KEYS = ["IMAGE_GEN_BASE_URL", "IMAGE_GEN_MODEL", "IMAGE_GEN_SIZE", "IMAGE_GEN_QUALITY", "IMAGE_GEN_RESPONSE_FORMAT",
  "IMAGE_GEN_STREAM", "IMAGE_GEN_FALLBACK_BASE_URL", "IMAGE_GEN_IMAGE_FIELD", "IMAGE_GEN_PROMPT", "IMAGE_GEN_ENV_FILE", "OPENAI_BASE_URL"];

async function loadDotEnv(args) {
  const explicit = firstArg(args, "env-file", process.env.IMAGE_GEN_ENV_FILE || "");
  const candidates = explicit ? [explicit] : [path.join(process.cwd(), ".env"), ...userEnvPaths()];
  const values = {};
  for (const candidate of [...new Set(candidates)]) {
    try {
      // 越靠前的文件优先级越高，后面的文件不覆盖已经读到的键。
      for (const [key, value] of Object.entries(parseDotEnv(await readFile(candidate, "utf8")))) {
        if (!(key in values)) values[key] = value;
      }
    } catch (error) {
      if (error.code === "ENOENT") {
        if (explicit) throw new UserError("env_file_not_found", "指定的 .env 文件不存在", { envFile: candidate });
        continue;
      }
      if (explicit) throw new UserError("env_file_unreadable", "指定的 .env 文件无法读取", { envFile: candidate });
    }
  }
  for (const key of ENV_KEYS) {
    if (values[key] && !process.env[key]) process.env[key] = values[key];
  }
  return values;
}

async function resolveApiKey(args) {
  const envFile = await loadDotEnv(args);
  const apiKey = firstArg(args, "api-key") || process.env.IMAGE_GEN_API_KEY || process.env.OPENAI_API_KEY ||
    envFile.IMAGE_GEN_API_KEY || envFile.OPENAI_API_KEY || "";
  if (!apiKey) {
    throw new UserError(
      "missing_api_key",
      "缺少 API Key。请用 --api-key 传入，或设置 IMAGE_GEN_API_KEY / OPENAI_API_KEY 环境变量，或在 .env 中写一行 IMAGE_GEN_API_KEY=你的密钥。",
      { key: "IMAGE_GEN_API_KEY", envVars: ["IMAGE_GEN_API_KEY", "OPENAI_API_KEY"] },
    );
  }
  return apiKey;
}

async function fetchBytes(url, timeoutSeconds, { expectImage = false } = {}) {
  let response;
  try {
    response = await fetch(url, { signal: AbortSignal.timeout(Math.max(1, timeoutSeconds) * 1000) });
  } catch (error) {
    throw new UserError("image_url_fetch_failed", "图片链接无法打开", { image: url, reason: error.message });
  }
  if (!response.ok) {
    throw new UserError("image_url_http_error", "图片链接返回非成功状态码", { image: url, status: response.status });
  }
  const contentType = response.headers.get("content-type")?.split(";")[0] || "application/octet-stream";
  if (expectImage && !contentType.startsWith("image/")) {
    throw new UserError("image_url_not_image", "图片链接返回的内容不是图片", { image: url, contentType });
  }
  return { bytes: await response.arrayBuffer(), contentType };
}

async function fetchImageUrl(url, timeoutSeconds) {
  const { bytes, contentType } = await fetchBytes(url, timeoutSeconds, { expectImage: true });
  let name = `source.${extensionFromContentType(contentType)}`;
  try {
    name = path.basename(new URL(url).pathname) || name;
  } catch {
    // 无法解析路径时使用默认文件名
  }
  return { bytes, contentType, name };
}

function dataUrlToFile(value) {
  const match = value.match(/^data:([^;,]+)?;base64,([\s\S]+)$/i);
  if (!match) {
    throw new UserError("invalid_image_data_url", "图片 data URL 无效，必须使用 data:image/...;base64,... 格式");
  }
  const contentType = match[1] || "image/png";
  if (!contentType.toLowerCase().startsWith("image/")) {
    throw new UserError("image_data_url_not_image", "图片 data URL 的 MIME 类型不是 image/*");
  }
  return { bytes: Buffer.from(match[2], "base64"), contentType, name: `source.${extensionFromContentType(contentType)}` };
}

async function imageToMultipartFile(image, timeoutSeconds) {
  if (/^https?:\/\//i.test(image)) return fetchImageUrl(image, timeoutSeconds);
  if (/^data:/i.test(image)) return dataUrlToFile(image);
  try {
    const bytes = await readFile(image);
    return { bytes, contentType: contentTypeFromPath(image), name: path.basename(image) || "source.png" };
  } catch {
    throw new UserError("image_path_not_found", "本地图片路径不存在或不可读取", { image });
  }
}

function buildMultipartForm(fields, files, fieldName) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null && value !== "") form.append(key, String(value));
  }
  for (const file of files) {
    form.append(fieldName, new Blob([file.bytes], { type: file.contentType }), file.name);
  }
  return form;
}

function requestHeaders(apiKey, stream, extra, accept) {
  return {
    Authorization: `Bearer ${apiKey}`,
    Accept: accept || (stream ? "text/event-stream" : "application/json"),
    ...extra,
  };
}

function timeoutSignal(seconds) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), seconds * 1000);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function fetchWithTimeout(url, options, timeoutSeconds) {
  const timeout = timeoutSignal(timeoutSeconds);
  try {
    return await fetch(url, { ...options, signal: timeout.signal });
  } catch (error) {
    if (error.name === "AbortError") throw new UserError("request_timeout", "接口请求超时", { timeout: timeoutSeconds, url });
    throw new UserError("network_error", "接口请求网络失败", { url, reason: error.message });
  } finally {
    timeout.clear();
  }
}

async function requestWithFallback(request, args) {
  const primaryUrl = request.url;
  try {
    return await fetchWithTimeout(primaryUrl, request.optionsFactory(), request.timeout);
  } catch (error) {
    if (!["network_error", "request_timeout"].includes(error.code)) throw error;
    const fallbackBase = firstArg(args, "fallback-base-url", defaultFallbackBaseUrl());
    if (!fallbackBase) throw error;
    const fallbackUrl = buildApiUrl(fallbackBase, request.path);
    if (fallbackUrl === primaryUrl) throw error;
    try {
      const response = await fetchWithTimeout(fallbackUrl, request.optionsFactory(), request.timeout);
      request.url = fallbackUrl;
      return response;
    } catch (fallbackError) {
      throw new UserError(error.code, `${error.message}；备用地址也无法连接`, {
        primary: error.detail,
        fallback: fallbackError.detail || { url: fallbackUrl },
      });
    }
  }
}

async function ensureOkResponse(response, url) {
  if (response.ok) return;
  throw new UserError("api_http_error", "生图接口返回非成功状态码", {
    url,
    status: response.status,
    body: await response.text().catch(() => ""),
  });
}

async function* readSseJson(response) {
  if (!response.body) throw new UserError("stream_unreadable", "响应没有可读取的流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const parseFrame = (frame) => {
    const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart()).join("\n").trim();
    if (!data || data === "[DONE]" || data === "[done]") return null;
    try {
      return JSON.parse(data);
    } catch (error) {
      throw new UserError("sse_json_parse_error", "SSE data 不是合法 JSON", { data, reason: error.message });
    }
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const event = parseFrame(frame);
      if (event) yield event;
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) {
    const event = parseFrame(buffer);
    if (event) yield event;
  }
}

async function* readImageEvents(response) {
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("text/event-stream")) {
    yield* readSseJson(response);
    return;
  }
  if (contentType.includes("application/json")) {
    try {
      yield await response.json();
      return;
    } catch (error) {
      throw new UserError("json_parse_error", "图片接口返回的 JSON 无法解析", { reason: error.message });
    }
  }
  const text = await response.text();
  try {
    yield JSON.parse(text);
    return;
  } catch {
    // 不是整体 JSON，按 SSE 逐帧解析
  }
  yield* readSseJson(new Response(text));
}

function isPartialImageEvent(event) {
  return event?.type === "image_generation.partial_image" || event?.type === "response.image_generation_call.partial_image";
}

function throwIfErrorEvent(event) {
  const failed = event?.type === "error" || event?.type === "response.failed" || event?.type === "failed";
  if (!failed && !(event?.error && typeof event.error === "object")) return;
  const message = event?.response?.error?.message || event?.error?.message || event?.message || "";
  if (!failed && !message) return;
  throw new UserError("api_stream_error", "生图接口返回错误", { type: event?.type, message });
}

function asList(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return [value];
  return [];
}

function collectRefs(event, refs = []) {
  if (!event || typeof event !== "object") return refs;
  for (const key of B64_KEYS) {
    if (typeof event[key] === "string" && event[key]) refs.push({ kind: "b64", value: event[key] });
  }
  for (const key of URL_KEYS) {
    if (typeof event[key] === "string" && /^https?:\/\//i.test(event[key])) refs.push({ kind: "url", value: event[key] });
  }
  for (const key of ["data", "images", "output", "results", "artifacts"]) {
    for (const item of asList(event[key])) collectRefs(item, refs);
  }
  for (const item of asList(event.response?.output)) collectRefs(item, refs);
  collectRefs(event.item, refs);
  return refs;
}

function outputPathFor(out, index, total, defaultName) {
  const target = out || defaultName;
  const ext = path.extname(target);
  const isDirectory = target.endsWith("/") || target.endsWith("\\") || !ext;
  if (isDirectory) {
    const name = total > 1 ? defaultName.replace(/(\.[^.]+)$/, `-${index + 1}$1`) : defaultName;
    return path.resolve(target, name);
  }
  if (total === 1) return path.resolve(target);
  return path.resolve(path.dirname(target), `${path.basename(target, ext)}-${index + 1}${ext}`);
}

async function saveRefs(refs, out, defaultName, timeout) {
  const paths = [];
  for (let i = 0; i < refs.length; i += 1) {
    let bytes;
    let fallbackName = defaultName;
    if (refs[i].kind === "b64") {
      bytes = Buffer.from(refs[i].value, "base64");
    } else {
      const fetched = await fetchBytes(refs[i].value, timeout);
      bytes = Buffer.from(fetched.bytes);
      if (!out) fallbackName = `generated-image.${extensionFromContentType(fetched.contentType)}`;
    }
    const filePath = outputPathFor(out, i, refs.length, fallbackName);
    await mkdir(path.dirname(filePath), { recursive: true });
    await writeFile(filePath, bytes);
    paths.push(filePath);
  }
  return paths;
}

async function buildRequest(args, mode, apiKey) {
  const timeout = intArg(args, "timeout", 900);
  const prompt = firstArg(args, "prompt");
  const baseUrl = firstArg(args, "base-url", defaultBaseUrl());
  const model = firstArg(args, "model", defaultModel());
  const stream = boolArg(args, "stream", defaultStream());
  const headers = extraHeaders(args);
  const quality = firstArg(args, "quality", defaultQuality());
  const responseFormat = firstArg(args, "response-format", defaultResponseFormat());

  // 只提交必要参数：不同服务端对未知/不兼容参数的容忍度差别很大。
  const fields = { model, n: intArg(args, "n", 1), size: firstArg(args, "size", defaultSize()), ...extraParams(args) };
  if (quality) fields.quality = quality;
  if (responseFormat) fields.response_format = responseFormat;
  if (stream) fields.stream = true;

  if (mode === "text") {
    if (!prompt) throw new UserError("missing_argument", "text 模式必须传入 --prompt", { key: "prompt" });
    const body = { ...fields, prompt };
    return {
      path: GENERATIONS_PATH,
      url: buildApiUrl(baseUrl, GENERATIONS_PATH),
      defaultOut: "generated-image.png",
      timeout,
      model,
      optionsFactory: () => ({
        method: "POST",
        headers: requestHeaders(apiKey, stream, headers),
        body: JSON.stringify(body),
      }),
    };
  }

  const images = listArg(args, "image").concat(listArg(args, "images"));
  if (!images.length) throw new UserError("missing_argument", `${mode} 模式至少传入一个 --image`, { key: "image", mode });
  if (mode === "image-prompt" && !prompt) throw new UserError("missing_argument", "image-prompt 模式必须传入 --prompt", { key: "prompt" });
  const files = [];
  for (const image of images) files.push(await imageToMultipartFile(image, timeout));
  const body = { ...fields, prompt: prompt || defaultImagePrompt() };
  const fieldName = firstArg(args, "image-field", defaultImageField());
  return {
    path: EDITS_PATH,
    url: buildApiUrl(baseUrl, EDITS_PATH),
    defaultOut: "generated-image.png",
    timeout,
    model,
    optionsFactory: () => ({
      method: "POST",
      headers: requestHeaders(apiKey, stream, headers, "*/*"),
      body: buildMultipartForm(body, files, fieldName),
    }),
  };
}

async function main() {
  const args = parseArgs();
  if (args.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  const mode = normalizeMode(firstArg(args, "mode", "text"));
  const apiKey = await resolveApiKey(args);
  const request = await buildRequest(args, mode, apiKey);
  const response = await requestWithFallback(request, args);
  await ensureOkResponse(response, request.url);
  for await (const event of readImageEvents(response)) {
    throwIfErrorEvent(event);
    if (isPartialImageEvent(event)) continue;
    const refs = collectRefs(event);
    if (refs.length) {
      const limit = intArg(args, "n", 1);
      const paths = await saveRefs(refs.slice(0, limit), firstArg(args, "out"), request.defaultOut, request.timeout);
      process.stdout.write(`${JSON.stringify({ ok: true, mode, model: request.model, paths, baseUrl: new URL(request.url).origin }, null, 2)}\n`);
      return;
    }
  }
  throw new UserError("no_image_result", "响应结束前没有收到图片结果数据", { mode, url: request.url });
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ ok: false, code: error.code || "unexpected_error", message: error.message || String(error), detail: error.detail || {} }, null, 2)}\n`);
  process.exitCode = 1;
});
