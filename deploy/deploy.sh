#!/usr/bin/env bash
# 一键发布：本地构建 wheel → 上传 gz-cvm → 安装 → 重启 → sha 校验 → deep_check。
# 用法：./deploy/deploy.sh [version]     # 无参 = 用当前工作区（含未提交改动）
set -euo pipefail

REMOTE="${SLIDEX_DEPLOY_REMOTE:-gz-cvm}"          # ssh 主机别名
REMOTE_DIR="${SLIDEX_DEPLOY_DIR:-/opt/slidex-captcha}"
VENV="${SLIDEX_DEPLOY_VENV:-$REMOTE_DIR/.venv}"
CHECK_DIR="${SLIDEX_DEPLOY_CHECK:-$REMOTE_DIR/check}"
BUILD_VENV="${SLIDEX_BUILD_VENV:-/tmp/slidex-build}"
# 各机器服务绑定的 mesh IP（health 检查与 deep_check 的 SLIDEX_BASE 都用它）
# huawei-sh = 华为云上海 10.144.144.9（gz-cvm 2026-11-10 到期后的接替机）
case "$REMOTE" in
  gz-cvm)    MESH_IP="${SLIDEX_MESH_IP:-10.144.144.8}" ;;
  mesh-huawei|huawei-sh) MESH_IP="${SLIDEX_MESH_IP:-10.144.144.9}" ;;
  *)         MESH_IP="${SLIDEX_MESH_IP:-}" ;;   # 未登记机器需显式给 SLIDEX_MESH_IP
esac
if [ -z "$MESH_IP" ]; then
  echo "unknown REMOTE '$REMOTE': set SLIDEX_MESH_IP=<service bind ip>" >&2
  exit 1
fi
# 部分机器（如 mesh-huawei）直接 root 登录、没有 sudo；有 sudo 才用
SUDO=$(ssh "$REMOTE" 'command -v sudo >/dev/null 2>&1 && echo sudo || true')

cd "$(dirname "$0")/.."
VERSION="${1:-$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')}"
WHEEL="dist/slidex-$VERSION-py3-none-any.whl"

echo "==> [1/6] build wheel (version $VERSION)"
if [ -n "${1:-}" ] && ! git diff-index --quiet HEAD -- 2>/dev/null; then
  echo "    WARNING: 工作区有未提交改动，指定版本发布时请先提交/tag" >&2
fi
[ -x "$BUILD_VENV/bin/python" ] || { echo "missing build venv: $BUILD_VENV" >&2; exit 1; }
rm -rf dist build/slidex
"$BUILD_VENV/bin/python" -m build --wheel --outdir dist >/dev/null
[ -f "$WHEEL" ] || { echo "wheel not found: $WHEEL" >&2; exit 1; }

echo "==> [2/6] upload"
scp -q "$WHEEL" "$REMOTE:/tmp/"

echo "==> [3/6] install + restart"
ssh "$REMOTE" "set -e
  # 安装器自适应：pip venv（gz-cvm 时代）用 pip；uv venv（mesh-huawei，无 pip 模块）回退 uv
  if $VENV/bin/python -m pip --version >/dev/null 2>&1; then
    $SUDO $VENV/bin/python -m pip install --force-reinstall --no-deps -q /tmp/$(basename "$WHEEL")
  elif command -v uv >/dev/null 2>&1; then
    $SUDO uv pip install --python $VENV/bin/python --force-reinstall --no-deps -q /tmp/$(basename "$WHEEL")
  else
    echo 'neither pip nor uv available in venv/host' >&2; exit 1
  fi
  $SUDO systemctl restart slidex-captcha
  sleep 3
  systemctl is-active slidex-captcha >/dev/null || { journalctl -u slidex-captcha -n 30 --no-pager; exit 1; }
  $VENV/bin/python -c 'import importlib.metadata as m; print(\"    version:\", m.version(\"slidex\"))'"

echo "==> [4/6] sha verify (local vs remote solver.py)"
LOCAL_SHA=$(shasum -a 256 slidex/px/solver.py | cut -d' ' -f1)
REMOTE_SHA=$(ssh "$REMOTE" "sha256sum $VENV/lib/python3.*/site-packages/slidex/px/solver.py | cut -d' ' -f1")
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  echo "    SHA MISMATCH: $LOCAL_SHA vs $REMOTE_SHA" >&2; exit 1
fi
echo "    sha ok: ${LOCAL_SHA:0:16}…"

# wheel 重装（--no-deps）不会带回浏览器缓存；换机/清缓存后 /health 照绿但
# 求解全挂（2026-11-14 线上验收实锤：deep_check 9/23，14 项 browser_launch_failed）。
# 双二进制校验：solver 走 launch(headless=True) 用 headless shell（必须存在）；
# 完整 chromium（executable_path 指向的）缺失时仅告警——有头模式才用。
# 注意 executable_path 永远指完整 chromium，直接拿它判会漏报 headless 缺失。
echo "==> [4.5/6] playwright browser preflight"
ssh -T "$REMOTE" "$VENV/bin/python - <<'PYEOF' 2>&1
import glob, os, sys
try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f'    playwright import failed: {e}', file=sys.stderr)
    sys.exit(1)
with sync_playwright() as p:
    full = p.chromium.executable_path
cache = os.path.dirname(os.path.dirname(os.path.dirname(full)))  # …/ms-playwright
# rev 不能从 executable_path 倒数第二级取——完整 chromium 缺失时该路径是期望值
# （…/chrome-linux64/chrome），那一级是平台名不是 rev。headless shell 目录名
# 自带 rev（chromium_headless_shell-<rev>），直接 glob。
shell_dirs = sorted(glob.glob(os.path.join(cache, 'chromium_headless_shell-*', 'chrome-headless-shell-*')))
shell = [os.path.join(d, 'chrome-headless-shell') for d in shell_dirs if os.path.isdir(d)]
shell_exists = bool(shell) and any(os.path.exists(s) for s in shell)
if not shell_exists:
    print(f'    HEADLESS SHELL MISSING: no chromium_headless_shell-*/ under {cache}', file=sys.stderr)
    print('    fix: playwright CDN is blocked from this host — download locally'
          ' (resumable curl loop through proxy), scp, unzip into'
          f' {cache}/chromium_headless_shell-<rev>/ keeping inner dir layout,'
          ' touch INSTALLATION_COMPLETE + chmod +x chrome-headless-shell', file=sys.stderr)
    sys.exit(1)
print(f'    headless shell ok: {shell[0]}')
if not os.path.exists(full):
    print(f'    WARNING: full chromium missing ({full}) — headless=False / 有头模式不可用', file=sys.stderr)
PYEOF
"

echo "==> [5/6] health"
ssh "$REMOTE" "curl -s --max-time 5 http://$MESH_IP:8077/health | head -c 200; echo"

echo "==> [6/6] deep_check"
# check 脚本以本仓为唯一真源：每次部署先把 repo 版本同步到目标机，
# 防止 server 侧热修导致 repo 副本漂移（review P3-3）。
ssh "$REMOTE" "$SUDO mkdir -p $CHECK_DIR && $SUDO tee $CHECK_DIR/deep_check.py >/dev/null" < deploy/check/deep_check.py
ssh "$REMOTE" "$SUDO tee $CHECK_DIR/cycle_check.py >/dev/null" < deploy/cycle_check.py
# 退出码门禁（review P2-1）：remote 无 pipefail 时 ssh 退出码取 tail 的 0，
# deep_check 失败会被吞掉——必须 set -o pipefail 让验收失败令部署失败。
ssh "$REMOTE" "set -o pipefail; SLIDEX_BASE=http://$MESH_IP:8077 $SUDO $VENV/bin/python $CHECK_DIR/deep_check.py 2>&1 | tail -2"

echo "✔ deployed $VERSION"
