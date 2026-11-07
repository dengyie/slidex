#!/usr/bin/env bash
# 同步线上验收工具到 gz-cvm（deploy.sh 只装 wheel，不含 check/）。
set -euo pipefail
REMOTE="${SLIDEX_DEPLOY_REMOTE:-gz-cvm}"
CHECK_DIR="${SLIDEX_DEPLOY_CHECK:-/opt/slidex-captcha/check}"

cd "$(dirname "$0")/.."
# deep_check 在仓库外维护的以 /tmp/slidex-deep 为源，这里从那里取
SRC="${SLIDEX_DEEP_SRC:-/tmp/slidex-deep}"
scp -q "$SRC/deep_check.py" "$REMOTE:/tmp/deep_check.py"
ssh "$REMOTE" "sudo mkdir -p $CHECK_DIR && sudo cp /tmp/deep_check.py $CHECK_DIR/"
echo "✔ synced: $SRC/deep_check.py -> $REMOTE:$CHECK_DIR/"
