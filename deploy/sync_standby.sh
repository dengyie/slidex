#!/usr/bin/env bash
# 冷备同步：把 wheel/env/check/nace 配置拉齐到备机，保证随时可切。
# 主 = mesh-huawei（华为云上海）；备 = mesh-mango2（pxed 实测 load 顶不住 chrome，
# 已降级为仅配置冷备）。备机切换用 systemctl（两台都是 systemd）。
# 用法：./deploy/sync_standby.sh          # 全量同步 + 备机自检
#       ./deploy/sync_standby.sh --check  # 只自检不传输
set -euo pipefail

STANDBY="${SLIDEX_STANDBY_REMOTE:-mesh-mango2}"
STANDBY_MESH_IP="${SLIDEX_STANDBY_MESH_IP:-10.144.144.88}"
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
    sudo grep -q '10.144.144.9:9999' $NACE_DIR/health_probe.sh && { echo 'stale probe with typo port 9999' >&2; exit 1; } || true
    echo \"    standby ok: slidex \$ver + env + check + nace config\"
    [ -f /etc/systemd/system/ha-probe.timer ] && echo '    standby ok: ha-probe.timer present' || echo '    WARN: ha-probe.timer missing (probe not self-healing on standby)'"
}

VERSION=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')

if [ "${1:-}" = "--check" ]; then
  standby_ok "$VERSION" || { echo "standby NOT in sync (want $VERSION)" >&2; exit 1; }
  exit 0
fi

echo "==> [1/4] wheel 0.9.x 同步（本地构建 → 备机安装）"
WHEEL=$(ls dist/slidex-$VERSION-py3-none-any.whl 2>/dev/null || true)
if [ -z "$WHEEL" ]; then
  [ -x /tmp/slidex-build/bin/python ] || { echo "missing build venv /tmp/slidex-build" >&2; exit 1; }
  rm -rf dist && /tmp/slidex-build/bin/python -m build --wheel --outdir dist >/dev/null
  WHEEL=dist/slidex-$VERSION-py3-none-any.whl
fi
scp -q "$WHEEL" "$STANDBY:/tmp/"
ssh "$STANDBY" "sudo /opt/slidex-captcha/.venv/bin/pip install --force-reinstall --no-deps -q /tmp/$(basename "$WHEEL") && rm /tmp/$(basename "$WHEEL")"

echo "==> [2/4] env 同步（经本机内存管道，不落本地盘）"
ssh "$ACTIVE" 'cat /etc/slidex-captcha.env' | ssh "$STANDBY" 'sudo tee /etc/slidex-captcha.env >/dev/null && sudo chmod 600 /etc/slidex-captcha.env'
# env 变更后必须重载备机服务（key 是 import 时读的；不同步重启会让备机 401）
ssh "$STANDBY" 'sudo systemctl restart slidex-captcha >/dev/null && sleep 3'

echo "==> [3/4] check 工具 + nace 配置对齐"
scp -q "$CHECK_DIR/deep_check.py" "$CHECK_DIR/cycle_check.py" "$STANDBY:/tmp/" 2>/dev/null || \
  scp -q /tmp/slidex-deep/deep_check.py deploy/cycle_check.py "$STANDBY:/tmp/"
ssh "$STANDBY" "sudo mkdir -p $CHECK_DIR && sudo mv /tmp/deep_check.py /tmp/cycle_check.py $CHECK_DIR/ && sudo sed -i 's|http://10.144.144.9:8077|http://$STANDBY_MESH_IP:8077|' $CHECK_DIR/cycle_check.py"
# nace：配置与探针脚本拉齐（探针为自机化版本：探自身 mesh IP，nace 未运行自动跳过）
ssh "$ACTIVE" "cat $NACE_DIR/config/egress.yaml" | ssh "$STANDBY" "sudo tee $NACE_DIR/config/egress.yaml >/dev/null"
ssh "$ACTIVE" "cat $NACE_DIR/health_probe.sh" | ssh "$STANDBY" "sudo tee $NACE_DIR/health_probe.sh >/dev/null && sudo chmod 700 $NACE_DIR/health_probe.sh && sudo bash -n $NACE_DIR/health_probe.sh"
# 探针 systemd 单元同步（若备机尚无则装上；timer enabled 但备机 nace 不常驻，脚本自带跳过）
ssh "$STANDBY" '[ -f /etc/systemd/system/ha-probe.timer ] || { echo "WARN: ha-probe units missing on standby, install manually" >&2; }'

echo "==> [4/4] 备机自检"
standby_ok "$VERSION"

echo "✔ standby in sync ($STANDBY = slidex $VERSION)。切换："
echo "  ssh $STANDBY 'sudo systemctl restart slidex-captcha'"
echo "  调用方 CAPTCHA_RUN_BASE 改指 http://$STANDBY_MESH_IP:8077"
