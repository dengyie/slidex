#!/usr/bin/env bash
# pxed 冷备同步：把 wheel/env/check/nace 配置拉齐到 pxed，保证随时可切。
# 用法：./deploy/sync_standby.sh          # 全量同步 + 备机自检
#       ./deploy/sync_standby.sh --check  # 只自检不传输
set -euo pipefail

STANDBY="${SLIDEX_STANDBY_REMOTE:-mesh-pxed}"
ACTIVE="${SLIDEX_ACTIVE_REMOTE:-mesh-huawei}"
REMOTE_DIR="/opt/slidex-captcha"
VENV="$REMOTE_DIR/.venv"
CHECK_DIR="$REMOTE_DIR/check"
NACE_DIR="/opt/nace-egress"
STANDBY_VENV="/opt/slidex-captcha/.venv"

cd "$(dirname "$0")/.."

standby_ok() {
  ssh "$STANDBY" "set -e
    [ -x $STANDBY_VENV/bin/python ]
    ver=\$($STANDBY_VENV/bin/python -c 'import importlib.metadata as m; print(m.version(\"slidex\"))' 2>/dev/null)
    [ \"\$ver\" = \"$1\" ]
    [ -f /etc/slidex-captcha.env ] && [ -f $CHECK_DIR/deep_check.py ]
    [ -f $NACE_DIR/config/egress.yaml ] && [ -f $NACE_DIR/health_probe.sh ]
    echo \"    standby ok: slidex \$ver + env + check + nace config\""
}

VERSION=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')

if [ "${1:-}" = "--check" ]; then
  standby_ok "$VERSION" || { echo "standby NOT in sync (want $VERSION)" >&2; exit 1; }
  exit 0
fi

echo "==> [1/4] wheel 0.9.x 同步（本地构建 → pxed 安装）"
WHEEL=$(ls dist/slidex-$VERSION-py3-none-any.whl 2>/dev/null || true)
if [ -z "$WHEEL" ]; then
  [ -x /tmp/slidex-build/bin/python ] || { echo "missing build venv /tmp/slidex-build" >&2; exit 1; }
  rm -rf dist && /tmp/slidex-build/bin/python -m build --wheel --outdir dist >/dev/null
  WHEEL=dist/slidex-$VERSION-py3-none-any.whl
fi
scp -q "$WHEEL" "$STANDBY:/tmp/"
ssh "$STANDBY" "sudo /root/.local/bin/uv pip install --python $STANDBY_VENV/bin/python --force-reinstall --no-deps -q /tmp/$(basename "$WHEEL") && rm /tmp/$(basename "$WHEEL")"

echo "==> [2/4] env 同步（经本机内存管道，不落本地盘）"
ssh "$ACTIVE" 'cat /etc/slidex-captcha.env' | ssh "$STANDBY" 'cat > /etc/slidex-captcha.env && chmod 600 /etc/slidex-captcha.env'

echo "==> [3/4] check 工具 + nace 配置对齐"
scp -q "$CHECK_DIR/deep_check.py" "$CHECK_DIR/cycle_check.py" "$STANDBY:/tmp/" 2>/dev/null || \
  scp -q /tmp/slidex-deep/deep_check.py deploy/cycle_check.py "$STANDBY:/tmp/"
ssh "$STANDBY" "sudo mkdir -p $CHECK_DIR && sudo mv /tmp/deep_check.py /tmp/cycle_check.py $CHECK_DIR/ && sudo sed -i 's|http://10.144.144.9:8077|http://10.144.144.31:8077|' $CHECK_DIR/cycle_check.py"
# nace：配置与探针脚本拉齐（pxed 仍是 supervisor 形态，探针脚本供切换后使用）
ssh "$ACTIVE" "cat $NACE_DIR/config/egress.yaml" | ssh "$STANDBY" "sudo tee $NACE_DIR/config/egress.yaml >/dev/null"
ssh "$ACTIVE" "cat $NACE_DIR/health_probe.sh" | ssh "$STANDBY" "sudo tee $NACE_DIR/health_probe.sh >/dev/null && sudo chmod 700 $NACE_DIR/health_probe.sh"

echo "==> [4/4] 备机自检"
standby_ok "$VERSION"

echo "✔ standby in sync ($STANDBY = slidex $VERSION)。切换："
echo "  ssh $STANDBY 'sudo supervisorctl -c /personal/pxed/supervisord.conf restart slidex-captcha'"
echo "  调用方 CAPTCHA_RUN_BASE 改指 http://10.144.144.31:8077"
