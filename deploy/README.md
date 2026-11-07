# 部署（gz-cvm 常规形态）

目标形态：**单机 mesh 常驻服务**——gz-cvm（EasyTier mesh，10.144.144.8）
上 systemd 守护，业务方通过 `http://10.144.144.8:8077` + Bearer key 调用。

## 布局

```
/opt/slidex-captcha/.venv       # uv 建的运行环境（uv venv + pip install wheel）
/opt/slidex-captcha/check/      # deep_check.py 等线上验收工具
/etc/slidex-captcha.env         # 0600：SLIDEX_CAPTCHA_KEYS / SLIDEX_PX_CONCURRENCY / SLIDEX_PX_HEADLESS
/etc/systemd/system/slidex-captcha.service
```

## 首次安装（新机器）

```bash
# 1. 基础依赖（TencentOS/OpenEuler 系；Debian 系替换为 chromium + xvfb 包名）
sudo dnf install -y python3.11 uv chromium-headlesschromium Xvfb nss

# 2. 建环境与目录
sudo mkdir -p /opt/slidex-captcha/check
sudo uv venv /opt/slidex-captcha/.venv --python 3.11

# 3. 服务账号与配置（从模板生成；KEY 自己生成，不要用示例值）
sudo install -m 600 deploy/slidex-captcha.env.example /etc/slidex-captcha.env
sudoedit /etc/slidex-captcha.env        # 填 SLIDEX_CAPTCHA_KEYS
sudo install -m 644 deploy/slidex-captcha.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now slidex-captcha
```

Playwright 浏览器内核（服务首次求解时会自动下载；离线机器预置：

```bash
sudo /opt/slidex-captcha/.venv/bin/python -m playwright install chromium-headless-shell
```

## 发布新版本（日常，一键）

仓库根目录：

```bash
./deploy/deploy.sh          # 当前工作区构建 → 上传 → 安装 → 重启 → sha 校验 → deep_check
./deploy/deploy.sh 0.9.3    # 或指定已 tag 的版本
```

脚本做 6 件事：本地 `python -m build --wheel` → `scp` 到 `/tmp/` →
`pip install --force-reinstall --no-deps` → `systemctl restart` →
`sha256sum` 对比本地与线上 solver.py → 跑 `deep_check.py`（23/23 才算过）。
deep_check 脚本由 `deploy/upload_check.sh` 单独同步到线上。

## 线上验收

| 工具 | 用途 |
|------|------|
| `check/deep_check.py` | 23 项验收：契约边界/上下文卫生/代理铁律/并发隔离/资源泄漏 |
| `check/heal_check.py` | 故障自愈：杀 Chromium 后服务应自动重建农场 |
| `GET /health` | `{ok, active_tasks, done_24h, fail_24h, types}` |

curl 冒烟：

```bash
curl -s http://10.144.144.8:8077/health
curl -s http://10.144.144.8:8077/v1/solve -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"type":"px_press","appId":"PXzC5j78di",...}'
```

## 运维注意

- **MemoryMax=1200M**：与 co-located 服务（balatro/openpet/decidex）共享
  1.9G 内存，超限只杀本 cgroup，不波及邻居。改配置先看整机水位。
- **workers=1**：并发求解靠 asyncio + 内部 semaphore（`SLIDEX_PX_CONCURRENCY`），
  不是多进程。浏览器农场是进程内单例，多 worker 会成倍吃内存。
- **密钥轮换**：`SLIDEX_CAPTCHA_KEYS` 逗号分隔多把，轮换时新旧并存、
  发新 key、业务方切换完再删旧 key，改完 `systemctl restart`。
- **磁盘**：wheel 安装只占 ~1M，但整台机磁盘紧张时先清
  `journalctl --vacuum-size=100M` 与浏览器缓存。
- **日志**：`journalctl -u slidex-captcha -f`；只出 token 前缀与长度，
  无完整 cookie/token（代码层保证）。
