"""cycle 压测验收：20 轮 × 4 路并发（deep_check D1/D2 同款 payload）。

验收口径：
- 全部 80 次求解 code=0（pass token）
- 每轮 context 隔离不串（D2 口径：自己 vid 在文档里，别人不在）
- 服务端无泄漏（收尾 active_tasks==0）
用法：sudo python cycle_check.py [rounds]
"""

import json
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "http://10.144.144.8:8077"  # gz-cvm 服务绑定在 mesh IP，非 127.0.0.1
KEY = ""
for line in open("/etc/slidex-captcha.env", encoding="utf-8"):
    if line.startswith("SLIDEX_CAPTCHA_KEYS="):
        KEY = line.split("=", 1)[1].strip()

HEADERS = {"authorization": f"Bearer {KEY}", "content-type": "application/json"}

ECHO_PAGE = b"""<html><body>echo<script>
document.cookie="_px3=1:a:1000:abcdef::; path=/";
document.cookie="_pxde={}; path=/";
document.cookie="_pxcts="+encodeURIComponent(document.cookie)+"; path=/";
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(ECHO_PAGE)))
        self.end_headers()
        self.wfile.write(ECHO_PAGE)

    def log_message(self, *a):
        pass


def call(path, payload, timeout=200):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(), headers=HEADERS, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def solve(payload, timeout=200):
    return call("/v1/solve", payload, timeout=timeout)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
echo_url = f"http://127.0.0.1:{srv.server_port}/"

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
CONC = 4

ok_rounds = 0
fail_codes = {}
crossed_total = 0
t0 = time.monotonic()
for rnd in range(ROUNDS):
    fan = [{
        "type": "px_press", "appId": "x", "mode": "silent",
        "websiteURL": echo_url,
        "cookies": {"_pxvid": f"cycle-{rnd}-{i}"},
        "timeout": 45,
    } for i in range(CONC)]
    outs = [None] * CONC

    def worker(i):
        outs[i] = solve(fan[i], timeout=90)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONC)]
    rs = time.monotonic()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    dur = time.monotonic() - rs

    codes = []
    crossed = 0
    for i, out in enumerate(outs):
        st, body = out
        d = json.loads(body)
        codes.append(d.get("code"))
        data = d.get("data") or {}
        doc = urllib.parse.unquote(str(data.get("pxcts") or ""))
        mine = f"_pxvid=cycle-{rnd}-{i}"
        if mine not in doc:
            crossed += 1
        if any(f"_pxvid=cycle-{rnd}-{j}" in doc for j in range(CONC) if j != i):
            crossed += 1
    for c in codes:
        if c != 0:
            fail_codes[c] = fail_codes.get(c, 0) + 1
    crossed_total += crossed
    if all(c == 0 for c in codes) and crossed == 0:
        ok_rounds += 1
    else:
        print(f"  [r{rnd:02d}] FAIL codes={codes} crossed={crossed} {dur:.1f}s")
    print(f"[r{rnd:02d}] {'ok' if all(c == 0 for c in codes) and crossed == 0 else 'FAIL'} "
          f"{dur:.1f}s", flush=True)

total_dur = time.monotonic() - t0
print()
print(f"== cycle summary ==")
print(f"rounds: {ok_rounds}/{ROUNDS} all-pass (concurrency {CONC}, "
      f"{ROUNDS * CONC} solves, {total_dur:.1f}s total)")
print(f"fail codes: {fail_codes or 'none'}")
print(f"isolation crossings: {crossed_total}")

# 收尾泄漏检查
import urllib.request as u
with u.urlopen(BASE + "/health", timeout=10) as r:
    h = json.loads(r.read().decode())
print(f"health: active_tasks={h.get('active_tasks')} done_24h={h.get('done_24h')} "
      f"fail_24h={h.get('fail_24h')}")
sys.exit(0 if ok_rounds == ROUNDS else 1)
