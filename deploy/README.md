# 部署

目标形态：**单机常驻服务**，业务方通过 `http://<host>:8077` + Bearer key 调用。

两种宿主形态：

| 宿主 | 进程守护 | 说明 |
|------|----------|------|
| gz-cvm（systemd） | `slidex-captcha.service` | 内存充裕，标准形态，见下文 |
| pxed（supervisor） | `supervisor-slidex-captcha.conf` | 4G 无 swap 共享宿主，**必须**额外装 oom_guard + renderer 限制，见文末 |

## gz-cvm 形态（systemd）

gz-cvm（EasyTier mesh，10.144.144.8）上 systemd 守护。

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

## pxed 形态（supervisor，低内存宿主）

pxed（mesh 10.144.144.31）无 systemd，走 supervisor；且整机 4G 无 swap、
overcommit=1、与其它服务共存——全局 OOM killer 曾专杀 chromium renderer
（默认 `oom_score_adj=300`，adj 越高越先被杀）。除标准步骤外**必须**做三件事：

### 安装

```bash
# 1. supervisor 程序配置（路径按宿主实际调整）
sudo cp deploy/supervisor-slidex-captcha.conf /personal/pxed/
sudo cp deploy/supervisor-slidex-oom-guard.conf /personal/pxed/
# 把两个文件路径加进 supervisord.conf 的 files= 列表，然后：
sudo supervisorctl reread && sudo supervisorctl update

# 2. oom_guard（每 1s 把 chrome-headless 的 oom_score_adj 钉到 -20，
#    无 CAP_SYS_RESOURCE 时可写到的下限；兜底覆盖 solver 自钉的空档）
sudo install -m 755 deploy/oom_guard.sh /opt/slidex-captcha/bin/oom_guard.sh
```

### pxed 专属环境变量（/etc/slidex-captcha.env）

```bash
SLIDEX_PX_CHROMIUM_ARGS=--renderer-process-limit=4
```

`--renderer-process-limit=4` 不只是省内存：没有它，deep_check B 组
（timezone/UA/locale 等指纹断言）会整组拿空值，22-23/23 → 15/23。
solver 通过 `SLIDEX_PX_CHROMIUM_ARGS` 透传（`_env_chromium_args()`）。

### 三层 OOM 防护（根因：dmesg 122 次 CONSTRAINT_NONE oom-killer 事件）

1. **oom_guard**（supervisor 程序，1s 循环）——兜底；
2. **solver 自钉** `_pin_oom_score_adj()`——launch 后、每个任务 new_context 前各钉一轮
   （新 fork 的 renderer 默认 adj=300，是 OOM 首选祭品）；
3. **renderer-process-limit=4**——限制 renderer 总量，兼保指纹断言。

注意 pxed 容器缺 `CAP_SYS_RESOURCE`：`oom_score_adj` 只能写到 **-20**
（supervisor 主进程的 adj），写 -500 会 EACCES——这是设计内行为，
guard 与 solver 的默认值都是 -20。

