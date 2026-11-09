"""深度验收（第二轮）：契约边界 / 上下文卫生 / 代理铁律 / 负载与泄漏。

只打合成页，不碰真实站点。每项独立记录，异常不中断。
运行在 gz-cvm 上，目标 10.144.144.8:8077。

C4 与 D 段是本轮补的：死端口只证明「连不上」会失败，真实会挂死的是
「连上了但永不回包」；负载段则看并发是否互相污染、浏览器/内存是否泄漏。
"""

import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import subprocess
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _default_base():
    """默认目标 = 本机 mesh IP（服务只绑 mesh 口）；SLIDEX_BASE 显式覆盖。

    历史默认写死 gz-cvm(10.144.144.8)，该机到期后谁拿着旧脚本跑就会把
    验收流量打到错误实例（2026-11-10 实测两次误报 1005）。改为自动探测
    （与 health_probe v2.1 同款 ip -o addr to 手法），env 仍可覆盖。
    """
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "to", "10.144.144.0/24"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        ip = out.split()[3].split("/")[0]
        if ip:
            return f"http://{ip}:8077"
    except Exception:
        pass
    return "http://127.0.0.1:8077"


BASE = os.environ.get("SLIDEX_BASE", "").strip() or _default_base()
KEY = ""
for line in open("/etc/slidex-captcha.env", encoding="utf-8"):
    if line.startswith("SLIDEX_CAPTCHA_KEYS="):
        KEY = line.split("=", 1)[1].strip()

HEADERS = {"authorization": f"Bearer {KEY}", "content-type": "application/json"}
results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def call(method, path, payload=None, *, headers=None, raw=None, timeout=200):
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else None
    )
    req = urllib.request.Request(
        BASE + path, data=data, headers=HEADERS if headers is None else headers, method=method
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), time.monotonic() - started
    except Exception as exc:
        return 0, f"<{type(exc).__name__}: {exc}>", time.monotonic() - started


def _chunk(tag, data):
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    + _chunk(b"IEND", b"")
)


def _pass_js(extra=""):
    return (
        '<script>' + extra +
        'document.cookie="_px3=1:a:1000:abcdef::; path=/";'
        'document.cookie="_pxde={}; path=/";'
        'document.cookie="_pxvid=vid-1; path=/";'
        'document.cookie="_pxcts=cts-1; path=/";'
        '</script>'
    )


PASS_PAGE = ("<html><body>ok" + _pass_js() + "</body></html>").encode()

# 把 context 环境写进 _pxde / _pxcts，靠返回值把它们带回来（沿用既有 cookie 抽取）
ENV_PAGE = (
    "<html><body>env<script>"
    "var tz='';try{tz=Intl.DateTimeFormat().resolvedOptions().timeZone}catch(e){}"
    "document.cookie='_px3=1:a:1000:abcdef::; path=/';"
    "document.cookie='_pxde='+encodeURIComponent(tz)+'; path=/';"
    "document.cookie='_pxvid='+encodeURIComponent(navigator.language)+'; path=/';"
    "document.cookie='_pxcts='+encodeURIComponent("
    "typeof navigator.webdriver+'|'+navigator.userAgent+'|'+document.referrer"
    ")+'; path=/';"
    "</script></body></html>"
).encode()

SLOW_PAGE = b"<html><body>nothing</body></html>"

# 页面把自己看到的 document.cookie 原样塞进 _pxcts（返回时 unquote 可读），
# 用于证明调用方 cookie 真的进了文档层，而不只是 Playwright 的 cookie jar。
ECHO_PAGE = (
    "<html><body>echo<script>"
    "document.cookie='_px3=1:a:1000:abcdef::; path=/';"
    "document.cookie='_pxcts='+encodeURIComponent(document.cookie.substr(0,300))+'; path=/';"
    "</script></body></html>"
).encode()


class Site:
    def __init__(self, body, status=200, delay=0.0):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                self.send_header("content-type", "text/html")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RecordingProxy:
    """记录代理：同时支持 plain-HTTP 绝对 URI（GET）与 CONNECT 隧道。

    Playwright/Chromium 对 http:// 目标走绝对 URI GET，对 https:// 走 CONNECT；
    只实现其中一种会把另一半误判成「没走代理」，所以两种都记。
    """

    def __init__(self, status=None):
        self.connects = []
        self.gets = []
        self.raw_lines = []
        self._status = status  # 非 None：所有请求一律回这个状态码

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_CONNECT(self):  # noqa: N802
                self.server.owner.connects.append(self.path)
                self.server.owner.raw_lines.append(
                    f"CONNECT {self.path} auth={self.headers.get('proxy-authorization')!r}"
                )
                if self.server.owner._status is not None:
                    self.send_response(self.server.owner._status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                host, _, port = self.path.partition(":")
                try:
                    upstream = socket.create_connection((host, int(port)), timeout=5)
                except Exception:
                    self.send_error(502)
                    return
                self.send_response(200, "Connection Established")
                self.end_headers()
                _relay(self.connection, upstream)

            def do_GET(self):  # noqa: N802
                self.server.owner.gets.append(self.path)
                self.server.owner.raw_lines.append(
                    f"GET {self.path} auth={self.headers.get('proxy-authorization')!r}"
                )
                if self.server.owner._status is not None:
                    self.send_response(self.server.owner._status)
                    self.send_header("Proxy-Authenticate", 'Basic realm="px"')
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                target = self.path
                if not target.startswith("http://"):
                    self.send_error(400)
                    return
                rest = target[len("http://"):]
                hostport, _, path = rest.partition("/")
                host, _, port = hostport.partition(":")
                try:
                    upstream = socket.create_connection(
                        (host, int(port or 80)), timeout=5
                    )
                except Exception:
                    self.send_error(502)
                    return
                upstream.sendall(
                    ("GET /" + path + " HTTP/1.0\r\nHost: " + hostport
                     + "\r\nConnection: close\r\n\r\n").encode()
                )
                while True:
                    data = upstream.recv(65536)
                    if not data:
                        break
                    try:
                        self.wfile.write(data)
                    except Exception:
                        break
                upstream.close()

            def log_message(self, *a):
                pass

        def _relay(a, b):
            def pump(src, dst):
                try:
                    while True:
                        chunk = src.recv(65536)
                        if not chunk:
                            break
                        dst.sendall(chunk)
                except Exception:
                    pass
                finally:
                    for sock in (src, dst):
                        try:
                            sock.close()
                        except Exception:
                            pass

            threading.Thread(target=pump, args=(a, b), daemon=True).start()
            pump(b, a)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Blackhole:
    """接受 TCP 连接但永不回包、也不主动关闭：验证导航超时兜底。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(50)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.held = []
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            self.held.append(conn)  # 只收着，不读不回

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        for conn in self.held:
            try:
                conn.close()
            except OSError:
                pass


def _detect_cgroup() -> str:
    """服务所在 cgroup：systemd slice（gz-cvm）或 kubepods（pxed/supervisor）。"""
    for cand in (
        "/sys/fs/cgroup/system.slice/slidex-captcha.service",
        "/sys/fs/cgroup",  # v2 根（有 cgroup.procs）
    ):
        try:
            with open(cand + "/cgroup.procs", encoding="utf-8"):
                return cand
        except OSError:
            continue
    return "/sys/fs/cgroup/memory"  # cgroup v1（pxed）：procs 走 tasks，内存走 usage


_CGROUP = _detect_cgroup()


def browser_procs():
    """服务 cgroup 内的 chrome-headless-shell 进程数（绝不动整机 Chrome）。"""
    procs = _CGROUP + "/cgroup.procs"
    try:
        with open(procs, encoding="utf-8") as fh:
            pids = [int(line) for line in fh if line.strip()]
    except OSError:
        try:
            with open(_CGROUP + "/tasks", encoding="utf-8") as fh:  # cgroup v1
                pids = [int(line) for line in fh if line.strip()]
        except OSError:
            return -1
    count = 0
    seen_tgids = set()
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ")
        except OSError:
            continue
        if b"chrome-headless-shell" not in cmd:
            continue
        # cgroup v1 的 tasks 是线程粒度：按 tgid 去重，进程数才是真实信号
        tgid = pid
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("Tgid:"):
                        tgid = int(line.split()[1])
                        break
        except OSError:
            pass
        if tgid in seen_tgids:
            continue
        seen_tgids.add(tgid)
        count += 1
    return count


def cgroup_mem_mb():
    for name in ("memory.current", "memory.usage_in_bytes"):  # v2, v1
        try:
            with open(_CGROUP + "/" + name, encoding="utf-8") as fh:
                return int(fh.read().strip()) / (1024 * 1024)
        except OSError:
            continue
    return -1.0


def solve(payload, timeout=200):
    return call("POST", "/v1/solve", payload, timeout=timeout)


def main():
    pass_site = Site(PASS_PAGE)
    env_site = Site(ENV_PAGE)
    slow_site = Site(SLOW_PAGE, delay=1.5)
    echo_cookie_site = Site(ECHO_PAGE)

    try:
        # ── A 契约边界 ────────────────────────────────────────────────
        st, body, _ = call("POST", "/v1/solve", {"type": "px_press", "appId": "x"},
                           headers={"authorization": "Bearer wrong-key"})
        record("A1 wrong key -> 401", st == 401 and KEY not in body,
               f"HTTP {st} body={body[:120]}")

        st, body, _ = call("POST", "/v1/solve", {"type": "px_press", "appId": "x"},
                           headers={"authorization": "Bearer "})
        record("A2 empty bearer on /v1/solve -> 401", st == 401, f"HTTP {st}")

        st, body, _ = call("POST", "/v1/solve", raw=b"{not json")
        record("A3 malformed JSON -> 4xx", 400 <= st < 500, f"HTTP {st} body={body[:120]}")

        st, body, _ = call("POST", "/v1/solve", raw=b"[1,2,3]")
        record("A4 non-object JSON -> 4xx", 400 <= st < 500, f"HTTP {st} body={body[:120]}")

        st, body, _ = solve({"type": "px_press", "appId": "x", "websiteURL": pass_site.url,
                             "mode": "silent", "cookies": "not-a-map"})
        record("A5 bad cookies type -> 4xx", 400 <= st < 500,
               f"HTTP {st} body={body[:140]}")

        st, body, _ = solve({"type": "px_press", "appId": "x", "websiteURL": pass_site.url,
                             "mode": "silent", "proxy": {"host": "1.2.3.4", "port": 70000}})
        record("A6 proxy port out of range -> 4xx", 400 <= st < 500,
               f"HTTP {st} body={body[:140]}")

        # 超时下限被夹到 10s：不写 cookie 的页面应在 ~10s 结束而非 1s/150s
        st, body, dur = solve({"type": "px_press", "appId": "x", "websiteURL": slow_site.url,
                               "mode": "silent", "pollInterval": 0.25, "timeout": 1},
                              timeout=200)
        d = json.loads(body)
        kind = (d.get("data") or {}).get("kind")
        record("A7 timeout clamped to >=10s", 9.0 <= dur <= 30.0,
               f"{dur:.1f}s kind={kind} (1s 会被夹到 10s)")

        # ── B 上下文卫生：时区 / UA / locale / 隐身脚本 / referer ──────
        explicit_tz = "Asia/Tokyo"
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 TestUA/9.9"
        st, body, dur = solve({
            "type": "px_press", "appId": "x", "mode": "silent",
            "websiteURL": env_site.url, "country": "JP", "timezone": explicit_tz,
            "locale": "ja-JP", "userAgent": ua, "timeout": 40,
        })
        d = json.loads(body)
        data = d.get("data") or {}
        tz_seen = urllib.parse.unquote(str(data.get("pxde") or ""))
        lang_seen = urllib.parse.unquote(str(data.get("pxvid") or ""))
        blob = urllib.parse.unquote(str(data.get("pxcts") or ""))
        parts = blob.split("|")
        webdriver_seen = parts[0] if parts else ""
        ua_seen = parts[1] if len(parts) > 1 else ""
        referer_seen = parts[2] if len(parts) > 2 else ""
        record("B1 timezone applied", tz_seen == explicit_tz, f"page saw tz={tz_seen!r}")
        record("B2 userAgent applied", ua_seen == ua, f"page saw UA={ua_seen[:40]!r}…")
        record("B3 locale applied", lang_seen == "ja-JP", f"page saw language={lang_seen!r}")
        record("B4 stealth: webdriver undefined", webdriver_seen == "undefined",
               f"typeof navigator.webdriver={webdriver_seen!r}")

        # v2 双页结构（§3.1）：press 先落宿主页（websiteURL），challengeUrl
        # 以 iframe 注入。iframe 文档的 document.referrer = 宿主页 origin
        # （跨源 strict-origin-when-cross-origin 只保留 origin）。
        # v2 契约：无 vid 的 press 会先自动补种子（合成站种不出 _pxvid，
        # 只能拿到 caller_vid_required），所以用例必须带 vid。
        host_site = Site(b"<html><body>host</body></html>")
        st, body, _ = solve({
            "type": "px_press", "appId": "x", "mode": "press",
            "websiteURL": host_site.url, "challengeUrl": env_site.url,
            "vid": "vid-deepcheck-b5", "timeout": 40,
        })
        d = json.loads(body)
        ref_seen = urllib.parse.unquote(str((d.get("data") or {}).get("pxcts") or "")).split("|")
        ref = ref_seen[2] if len(ref_seen) > 2 else ""
        record("B5 press challenge iframe carries host referer",
               ref.rstrip("/") == f"http://127.0.0.1:{host_site.port}",
               f"page saw referrer={ref!r} (host origin "
               f"http://127.0.0.1:{host_site.port})")

        # 调用方 cookie 注入：页面能读到被注入的 _pxvid。
        # 注入的 cookie 必须真正落在文档里（Document.cookie 可见），而不是只进
        # 了 jar；ECHO 页在覆盖前先抓 document.cookie 原文再作为结果输出。
        st, body, _ = solve({
            "type": "px_press", "appId": "x", "mode": "silent",
            "websiteURL": echo_cookie_site.url,
            "cookies": {"_pxvid": "vid-from-caller"},
            "timeout": 40,
        })
        d = json.loads(body)
        data = d.get("data") or {}
        doc_cookie = urllib.parse.unquote(str(data.get("pxcts") or ""))[:400]
        record("B6 caller cookie injected into document",
               "_pxvid=vid-from-caller" in doc_cookie,
               f"doc.cookie={doc_cookie!r}")

        # ── C 代理铁律（§4）：出站真的走代理 ─────────────────────────
        proxy = RecordingProxy()
        try:
            st, body, dur = solve({
                "type": "px_press", "appId": "x", "mode": "silent",
                "websiteURL": pass_site.url, "timeout": 40,
                "proxy": {"host": "127.0.0.1", "port": proxy.port},
            })
            d = json.loads(body)
            data = d.get("data") or {}
            # 目标站点是 http://：Chromium 走绝对 URI 的 plain GET，不是 CONNECT
            record("C1 egress goes through proxy",
                   d.get("code") == 0 and bool(proxy.gets or proxy.connects),
                   f"code={d.get('code')} GETs={proxy.gets[:3]} CONNECTs={proxy.connects[:3]} {dur:.1f}s")
            record("C2 proxy target is the synthetic host",
                   any(f":{pass_site.port}/" in g for g in proxy.gets)
                   or any(f":{pass_site.port}" in c for c in proxy.connects),
                   f"GETs={proxy.gets} CONNECTs={proxy.connects}")
        finally:
            proxy.close()

        # 死代理必须失败（不允许静默直连回退）
        st, body, dur = solve({
            "type": "px_press", "appId": "x", "mode": "silent",
            "websiteURL": pass_site.url, "pollInterval": 0.25, "timeout": 25,
            "proxy": {"host": "127.0.0.1", "port": 9},
        })
        d = json.loads(body)
        data = d.get("data") or {}
        record("C3 dead proxy fails (no direct fallback)",
               d.get("code") != 0 and data.get("kind") in
               {"challenge_load_failed", "challenge_load_timeout", "px_token_timeout"},
               f"code={d.get('code')} kind={data.get('kind')} {dur:.1f}s")

        # 黑洞代理：接受 TCP 但永不回包。死端口只证明「连不上」会失败，
        # 真正会挂死的是「连上了但没响应」——必须有导航超时兜底。
        hole = Blackhole()
        try:
            st, body, dur = solve({
                "type": "px_press", "appId": "x", "mode": "silent",
                "websiteURL": pass_site.url, "pollInterval": 0.25, "timeout": 12,
                "proxy": {"host": "127.0.0.1", "port": hole.port},
            })
            d = json.loads(body)
            data = d.get("data") or {}
            record("C4 blackhole proxy times out (budget honored)",
                   d.get("code") != 0
                   and data.get("kind") in {"challenge_load_timeout", "px_token_timeout"}
                   and dur <= 12 + 18,
                   f"code={d.get('code')} kind={data.get('kind')} {dur:.1f}s")
        finally:
            hole.close()

        # 代理要认证（407）：Chromium 把 407 当主帧文档渲染，必须在首屏就判死；
        # 否则会轮询到预算耗尽，返回一个误导性的 px_token_timeout（可重试）。
        auth_proxy = RecordingProxy(status=407)
        try:
            st, body, dur = solve({
                "type": "px_press", "appId": "x", "mode": "silent",
                "websiteURL": pass_site.url, "pollInterval": 0.25, "timeout": 12,
                "proxy": {"host": "127.0.0.1", "port": auth_proxy.port},
            })
            d = json.loads(body)
            data = d.get("data") or {}
            record("C5 proxy 407 fails fast and not retryable",
                   d.get("code") != 0
                   and data.get("kind") == "challenge_proxy_auth"
                   and data.get("retryable") is False
                   and dur < 25,
                   f"code={d.get('code')} kind={data.get('kind')} "
                   f"retryable={data.get('retryable')} {dur:.1f}s")
        finally:
            auth_proxy.close()

        # ── D 负载与泄漏 ──────────────────────────────────────────────
        # 基线取在并发之前；注意浏览器农场是懒建的，首次任务会拉起一个。
        base_browsers, base_mem = browser_procs(), cgroup_mem_mb()

        concurrency = 2
        fan = [{
            "type": "px_press", "appId": "x", "mode": "silent",
            "websiteURL": echo_cookie_site.url,
            "cookies": {"_pxvid": f"tenant-{i}"},
            "timeout": 45,
        } for i in range(concurrency)]
        outs = [None] * concurrency

        def worker(i):
            outs[i] = solve(fan[i], timeout=90)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
        started = time.monotonic()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        fan_dur = time.monotonic() - started

        codes, seen_vids, crossed = [], [], 0
        for i, out in enumerate(outs):
            if out is None:
                codes.append(None)
                continue
            _, body, _ = out
            try:
                d = json.loads(body)
            except Exception:
                codes.append(None)
                continue
            codes.append(d.get("code"))
            data = d.get("data") or {}
            doc = urllib.parse.unquote(str(data.get("pxcts") or ""))
            mine = f"_pxvid=tenant-{i}"
            if mine in doc:
                seen_vids.append(i)
            # 别的租户的 vid 出现在我的文档里 = context 串了
            if any(f"_pxvid=tenant-{j}" in doc for j in range(concurrency) if j != i):
                crossed += 1

        record("D1 concurrent fan all succeed",
               all(c == 0 for c in codes),
               f"codes={codes} total={fan_dur:.1f}s")
        record("D2 per-task cookie isolation",
               len(seen_vids) == concurrency and crossed == 0,
               f"own-vid seen in {len(seen_vids)}/{concurrency}, crossed={crossed}")

        # 泄漏：任务全结束后浏览器进程应回到基线附近，内存不持续爬升
        time.sleep(2.0)
        after_browsers, after_mem = browser_procs(), cgroup_mem_mb()
        record("D3 no chromium process leak",
               after_browsers >= 0 and after_browsers <= max(base_browsers, 1) + 2,
               f"browsers {base_browsers} -> {after_browsers}")
        # D4 阈值随 cgroup 限额自适应：gz-cvm systemd slice 1200M；
        # pxed kubepods 4G。读不到限额按 1200M 兜底。
        try:
            with open(_CGROUP + "/memory.limit_in_bytes", encoding="utf-8") as fh:
                mem_limit_mb = int(fh.read().strip()) / (1024 * 1024)
        except (OSError, ValueError):
            mem_limit_mb = 1200.0
        if not (0 < mem_limit_mb < 100000):  # v1 "无限制"=9223372036854771712
            mem_limit_mb = 1200.0
        mem_cap = mem_limit_mb * 0.5
        record("D4 cgroup memory not runaway",
               0 <= after_mem < mem_cap and (after_mem - base_mem) < 400,
               f"mem {base_mem:.0f}MB -> {after_mem:.0f}MB (cap {mem_cap:.0f}MB of limit {mem_limit_mb:.0f}MB)")

        # 收敛：任务结束后不应还挂着活跃任务
        st, body, _ = call("GET", "/health")
        try:
            health = json.loads(body)
        except Exception:
            health = {}
        record("D5 no dangling active tasks",
               health.get("active_tasks") == 0,
               f"health={body[:160]}")
    finally:
        pass_site.close()
        env_site.close()
        slow_site.close()
        echo_cookie_site.close()

    print("\n== summary ==")
    bad = [r for r in results if not r[1]]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    for name, ok, detail in bad:
        print(f"  FAILED {name}: {detail}")


if __name__ == "__main__":
    main()
