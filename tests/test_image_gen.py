#!/usr/bin/env python3
"""离线回归测试：用本机 mock 服务验证两个脚本，不需要网络和真实密钥。

运行：python3 tests/test_image_gen.py
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NODE_SCRIPT = REPO / "scripts" / "node" / "image-gen.js"
PYTHON_SCRIPT = REPO / "scripts" / "python" / "image_gen.py"

# 1x1 PNG，用作 mock 服务返回的图片内容。
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG).decode()

sys.path.insert(0, str(REPO / "scripts" / "python"))
import image_gen  # noqa: E402


class MockHandler(BaseHTTPRequestHandler):
    server_version = "MockImageGen/1.0"

    def log_message(self, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length) if length else b""

    def _respond_json(self):
        payload = {"created": 1, "data": [{"b64_json": PNG_B64, "revised_prompt": "mock"}]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_sse(self):
        frames = [
            {"type": "image_generation.partial_image", "b64_json": "UGFSVElBTA=="},
            {"type": "image_generation.completed", "b64_json": PNG_B64},
        ]
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames).encode() + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - http.server 约定
        body = self._read_body()
        type(self).calls.append({
            "path": self.path,
            "auth": self.headers.get("authorization"),
            "accept": self.headers.get("accept"),
            "content_type": self.headers.get("content-type") or "",
            "body": body,
        })
        streamed = b'"stream": true' in body or b'"stream":"true"' in body or "event-stream" in (self.headers.get("accept") or "")
        if b'"model": "force-error"' in body or b'"model":"force-error"' in body:
            payload = json.dumps({"error": {"message": "mock failure"}}).encode()
            self.send_response(500)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if streamed:
            self._respond_sse()
            return
        self._respond_json()


class ScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        MockHandler.calls = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockHandler.calls = []
        self.tmp = Path(tempfile.mkdtemp(prefix="imageskill-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()

    def run_env(self):
        # 隔离用户目录与项目目录里的 .env，保证测试只受显式参数影响。
        env = {key: value for key, value in os.environ.items() if not key.startswith(("IMAGE_GEN_", "OPENAI_"))}
        env.update({"HOME": str(self.home), "USERPROFILE": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "APPDATA": str(self.home / "AppData"), "PATH": os.environ.get("PATH", "")})
        return env

    def run_script(self, argv, env_extra=None, cwd=None):
        env = self.run_env()
        env.update(env_extra or {})
        return subprocess.run(argv, cwd=str(cwd or self.tmp), env=env, capture_output=True, text=True, timeout=120)

    def node(self, *argv, env_extra=None):
        if not shutil.which("node"):
            self.skipTest("未安装 Node.js，跳过脚本级测试")
        return self.run_script(["node", str(NODE_SCRIPT), *argv], env_extra=env_extra)

    def python(self, *argv, env_extra=None):
        return self.run_script([sys.executable, str(PYTHON_SCRIPT), *argv], env_extra=env_extra)

    def assert_saved(self, proc, expect_path=None):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["paths"]), 1)
        saved = Path(result["paths"][0])
        self.assertTrue(saved.exists(), saved)
        self.assertEqual(saved.read_bytes(), PNG)
        if expect_path:
            self.assertEqual(saved, Path(expect_path).resolve())
        return result

    # ---- 端到端 ----

    def test_node_text_json(self):
        proc = self.node("--mode", "text", "--base-url", self.base_url, "--api-key", "sk-test",
                         "--prompt", "一只戴宇航头盔的猫", "--out", str(self.tmp / "cat.png"))
        self.assert_saved(proc, self.tmp / "cat.png")
        call = MockHandler.calls[-1]
        self.assertEqual(call["path"], "/v1/images/generations")
        self.assertEqual(call["auth"], "Bearer sk-test")
        body = json.loads(call["body"])
        self.assertEqual(body["model"], "gpt-image-1")
        self.assertNotIn("stream", body)
        self.assertNotIn("quality", body)

    def test_python_text_json(self):
        proc = self.python("--mode", "text", "--base-url", self.base_url, "--api-key", "sk-test",
                           "--prompt", "城市夜景", "--out", str(self.tmp / "city.png"))
        self.assert_saved(proc, self.tmp / "city.png")

    def test_node_stream_sse(self):
        proc = self.node("--base-url", self.base_url, "--api-key", "sk-test", "--stream", "true",
                         "--prompt", "流式测试", "--model", "my-image-model", "--quality", "high",
                         "--param", "background=transparent", "--out", str(self.tmp / "sse.png"))
        self.assert_saved(proc, self.tmp / "sse.png")
        call = MockHandler.calls[-1]
        self.assertIn("text/event-stream", call["accept"])
        body = json.loads(call["body"])
        self.assertEqual(body["model"], "my-image-model")
        self.assertTrue(body["stream"])
        self.assertEqual(body["background"], "transparent")

    def test_python_stream_sse(self):
        proc = self.python("--base-url", self.base_url, "--api-key", "sk-test", "--stream", "true",
                           "--prompt", "流式测试", "--out", str(self.tmp / "sse.png"))
        self.assert_saved(proc, self.tmp / "sse.png")

    def test_node_edits_multipart(self):
        source = self.tmp / "ref.png"
        source.write_bytes(PNG)
        proc = self.node("--mode", "image", "--base-url", self.base_url, "--api-key", "sk-test",
                         "--image", str(source), "--out", str(self.tmp / "out.png"))
        self.assert_saved(proc, self.tmp / "out.png")
        call = MockHandler.calls[-1]
        self.assertEqual(call["path"], "/v1/images/edits")
        self.assertTrue(call["content_type"].startswith("multipart/form-data"))
        self.assertIn(b"filename=", call["body"])

    def test_python_edits_multipart(self):
        source = self.tmp / "ref.png"
        source.write_bytes(PNG)
        proc = self.python("--mode", "image-prompt", "--base-url", self.base_url, "--api-key", "sk-test",
                           "--image", str(source), "--prompt", "改成赛博朋克风格", "--out", str(self.tmp / "out.png"))
        self.assert_saved(proc, self.tmp / "out.png")
        body = MockHandler.calls[-1]["body"]
        self.assertIn(b'name="prompt"', body)

    def test_python_edits_image_field_override(self):
        source = self.tmp / "ref.png"
        source.write_bytes(PNG)
        proc = self.python("--mode", "image", "--base-url", self.base_url, "--api-key", "sk-test",
                           "--image", str(source), "--image-field", "image[]", "--out", str(self.tmp / "f.png"))
        self.assert_saved(proc, self.tmp / "f.png")
        self.assertIn(b'name="image[]"', MockHandler.calls[-1]["body"])

    def test_env_var_config(self):
        env = {"IMAGE_GEN_BASE_URL": self.base_url, "IMAGE_GEN_API_KEY": "sk-env", "IMAGE_GEN_MODEL": "env-model"}
        proc = self.python("--prompt", "环境变量", "--out", str(self.tmp / "env.png"), env_extra=env)
        self.assert_saved(proc, self.tmp / "env.png")
        self.assertEqual(json.loads(MockHandler.calls[-1]["body"])["model"], "env-model")
        self.assertEqual(MockHandler.calls[-1]["auth"], "Bearer sk-env")

    def test_dotenv_file(self):
        env_file = self.tmp / ".env"
        env_file.write_text(f"IMAGE_GEN_API_KEY=sk-dotenv\nIMAGE_GEN_BASE_URL={self.base_url}\n", encoding="utf-8")
        proc = self.python("--env-file", str(env_file), "--prompt", "dotenv", "--out", str(self.tmp / "d.png"))
        self.assert_saved(proc, self.tmp / "d.png")
        self.assertEqual(MockHandler.calls[-1]["auth"], "Bearer sk-dotenv")

    def test_missing_api_key(self):
        proc = self.python("--base-url", self.base_url, "--prompt", "无密钥")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stderr)["code"], "missing_api_key")
        self.assertNotIn("sk-", proc.stderr)

    def test_http_error_is_reported(self):
        proc = self.python("--base-url", self.base_url, "--api-key", "sk-test", "--model", "force-error",
                           "--prompt", "错误路径")
        self.assertNotEqual(proc.returncode, 0)
        detail = json.loads(proc.stderr)
        self.assertEqual(detail["code"], "api_http_error")
        self.assertEqual(detail["detail"]["status"], 500)

    def test_network_error_without_fallback(self):
        proc = self.python("--base-url", "http://127.0.0.1:1/v1", "--api-key", "sk-test", "--timeout", "5",
                           "--prompt", "无法连接")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(json.loads(proc.stderr)["code"], ("network_error", "request_timeout"))

    def test_mode_aliases_and_validation(self):
        source = self.tmp / "ref.png"
        source.write_bytes(PNG)
        bad = self.python("--mode", "banana", "--api-key", "sk-test", "--base-url", self.base_url)
        self.assertEqual(json.loads(bad.stderr)["code"], "invalid_mode")
        missing = self.python("--mode", "text", "--api-key", "sk-test", "--base-url", self.base_url)
        self.assertEqual(json.loads(missing.stderr)["code"], "missing_argument")
        absent = self.python("--mode", "image", "--api-key", "sk-test", "--base-url", self.base_url,
                             "--image", str(self.tmp / "nope.png"))
        self.assertEqual(json.loads(absent.stderr)["code"], "image_path_not_found")

    def test_help_prints_usage(self):
        for proc in (self.python("--help"), self.node("--help") if shutil.which("node") else None):
            if proc is None:
                continue
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("用法", proc.stdout)

    # ---- 纯函数单测（Python 实现）----

    def test_build_api_url(self):
        self.assertEqual(image_gen.build_api_url("https://api.openai.com/v1", "/v1/images/generations"),
                         "https://api.openai.com/v1/images/generations")
        self.assertEqual(image_gen.build_api_url("https://example.com", "/v1/images/edits"),
                         "https://example.com/v1/images/edits")
        self.assertEqual(image_gen.build_api_url("http://127.0.0.1:8000/", "/v1/images/generations"),
                         "http://127.0.0.1:8000/v1/images/generations")

    def test_normalize_mode(self):
        for alias, expected in (("edit", "image-prompt"), ("i2i", "image"), ("text-to-image", "text"), ("vision", "image-prompt")):
            self.assertEqual(image_gen.normalize_mode(alias), expected)

    def test_parse_dotenv(self):
        parsed = image_gen.parse_dotenv('export A=1\nB="two three"\n# comment\nC=\n')
        self.assertEqual(parsed, {"A": "1", "B": "two three", "C": ""})

    def test_collect_refs(self):
        event = {"data": [{"b64_json": "AAA"}], "output": [{"images": [{"url": "https://x/y.png"}]}]}
        self.assertEqual(image_gen.collect_refs(event), [{"kind": "b64", "value": "AAA"}, {"kind": "url", "value": "https://x/y.png"}])

    def test_no_provider_preset_left(self):
        # 任务要求：剔除原服务商预设，仓库里不应再出现该服务商域名的任何硬编码。
        for path in sorted(REPO.rglob("*")):
            if not path.is_file() or ".git" in path.parts or path.suffix in {".png", ".jpg", ".webp"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            banned = ["apikey" + ".fan", "apikey" + "-fan", "gpt-image-2.5-" + "sunburst", "grok-" + "imagine"]
            for needle in banned:
                self.assertNotIn(needle, text, f"{path.relative_to(REPO)} 仍包含 {needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
