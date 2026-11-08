#!/bin/bash
# slidex chrome OOM 护卫（低内存共享宿主用）：把本机 chrome-headless 进程的
# oom_score_adj 钉到 -20（无 CAP_SYS_RESOURCE 的容器能写到的最低值）。
# 根因背景：4G 无 swap + overcommit=1 的宿主上，全局 OOM killer 专挑 adj 高
# 的进程（chromium renderer 默认 300）。solver 内部另有 launch 后/每任务前
# 的自钉（_pin_oom_score_adj），本 guard 兜底覆盖两者空档间新生的进程。
# 只动 chrome 进程，每 1s 一轮。systemd 宿主通常内存充裕，可不装。
while true; do
  for p in $(pgrep -f chrome-headless 2>/dev/null); do
    cur=$(cat /proc/$p/oom_score_adj 2>/dev/null) || continue
    if [ "$cur" != "-20" ]; then
      echo -20 > /proc/$p/oom_score_adj 2>/dev/null
    fi
  done
  sleep 1
done
