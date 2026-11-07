# mp-publisher

潜界 AI 的独立微信公众号**草稿箱投递器**。内容生产项目将正式文章写入 Notion READY 专栏；本项目只从三个固定专栏读取原稿、校验来源与正文哈希、转换微信 HTML，再通过微信公众平台 `draft/add` 创建**未群发草稿**。

**运行位置：硅谷服务器 Docker 容器**。微信公众平台的 API IP 白名单包含该服务器的出口 IP，因此定时发布不能在 GitHub-hosted Actions 上执行。本仓库的 Actions **只跑 CI**，生产调度由服务器 systemd timer 启动临时容器，不运行常驻 HTTP 服务或定时轮询。

配套系统：独立 [cms-publisher](https://github.com/gamehack/cms-publisher) 负责 CMS 草稿（GitHub Actions），二者互不调用；原 [qjai-text-publisher](https://github.com/gamehack/qjai-text-publisher) 在安全切换后停止全部定时任务。本项目没有 CMS 客户端、CMS 密钥、CMS 草稿写入或公众号群发/公开发布能力。

## 流程与计划时间

```text
Grok / Product Hunt / TrustMRR → Notion 唯一 READY 正文
  → 硅谷 systemd timer → docker compose run --rm mp-publisher
  → 校验直接父页面、标题、来源键、SHA-256
  → 私有 SQLite 微信幂等状态 → 微信 token / draft/add
  → 微信公众号草稿箱（不群发）
```

| Profile | Notion 父页面 | source_key | 北京时间 |
|---|---|---|---|
| `x` | `4019e605ccf881939c2ddbe20e3112fa` | `x-daily:YYYY-MM-DD` | 每天 07:30 |
| `producthunt` | `4109e605ccf881c4accdc19386b4a38d` | `producthunt:YYYY-MM-DD` | 每天 17:30、18:30 兜底 |
| `trustmrr` | `4109e605ccf881eda46cf9ab95538f5b` | `trustmrr-weekly:YYYY-MM-DD` / `trustmrr-monthly:YYYY-MM` | 周一 03:00；首个周一还检查月报 |

- Product Hunt 的周期以 `America/Los_Angeles` 上一个完整自然日为准，其余调度使用 `Asia/Shanghai`。
- 没有 READY 或校验失败绝不投递；缺失内容可由下一固定窗口处理。`Persistent=false`，不因关机自动补发历史稿件。
- 微信凭据校验先于 `draft/add`；发起外部请求前写入 `INTENT`，任何不确定结果记为 `UNKNOWN`，必须人工在公众号后台核验后才能恢复。已成功的 `source_key` 再次运行只返回 `ALREADY_DONE`。
- 私有 SQLite 路径 `/var/lib/mp-publisher/state.sqlite`（容器内 `/data/state.sqlite`），保留文章快照、微信回执与人工对账记录。镜像和 GitHub 均不保存正文、数据库或密钥。

## 代码开发与 CI

Python 3.11+，不依赖数据库服务。`CI` 仅运行测试与语法校验：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

正式命令：

```bash
# 仅读取 Notion，不调用微信 draft/add
mp-publisher dry-run --profile producthunt --period 2026-11-06

# 只检查白名单 IP 与微信凭据（GET token；不创建草稿，也不打印 token）
mp-publisher check-wechat

# 查询私有 SQLite 中的微信投递状态
mp-publisher status --profile producthunt
mp-publisher health

# 指定一次历史周期；必须由人工明确授权
mp-publisher backfill --profile producthunt --period 2026-11-06 --authorize
```

需要有效的双重开关 `WECHAT_ENABLE_DRAFTS=true`、`WECHAT_<PROFILE>_ENABLED=true` 和 `PUBLISH_<PROFILE>_ENABLED=true` 才允许 `run/backfill` 写入草稿。所有开关缺省均关闭。

## Docker 生产部署

- 代码目录：`/opt/mp-publisher`。
- 生产环境变量：`/etc/mp-publisher/publisher.env`（root 拥有，`0600`）。
- 私有状态：`/var/lib/mp-publisher/state.sqlite`（root 拥有，`0600`），state 目录 `0700`。
- 无公开监听端口，使用 Docker 默认 bridge 网络的服务器出口 IP。`check-wechat` 必须在**容器内**通过，确认该出口 IP 在公众号白名单。
- 镜像文件系统只读，除 `/data` 和临时 `/tmp` 外无写入挂载；移除 Linux capabilities、禁止新权限。
- `docker compose run --rm` 每个定时任务执行完即退出，不占常驻容器资源。

### 初次切换（严禁新旧发布器同时调度）

1. 检查旧 `qjai-text-publisher` 的三组 timer 和当下正在运行的服务，确认不存在执行中的微信 `draft/add` 或 `UNKNOWN/INTENT` 未对账记录；检查 `cms-publisher` 已独立运行。
2. 检出此仓库至 `/opt/mp-publisher`；核对固定版本、`docker compose version`、Docker 状态，运行测试。**此时不启用新 timer，不触发写入。**
3. 创建 root 私有目录：`install -d -o root -g root -m 0700 /etc/mp-publisher /var/lib/mp-publisher`。
4. 由 root 运行 `python3 scripts/migrate-env.py --source /opt/qjai-text-publisher/.env --dest /etc/mp-publisher/publisher.env`。此脚本仅复制 Notion 与微信 allowlist 环境变量，**不复制 CMS token 或旧数据库路径，不打印密钥**；目标已存在时拒绝覆盖。检查开关值但不要输出密钥。
5. 等待旧任务结束后执行 `systemctl disable --now` 旧三组 timer。**此时为切换窗口起点**；禁止旧任务再次执行。
6. 使用旧项目经过测试的 SQLite 在线备份命令，将旧状态一致性迁移为新状态：

   ```bash
   cd /opt/qjai-text-publisher
   .venv/bin/qjai-text-publisher --env-file .env \
     backup-state --output /var/lib/mp-publisher/state.sqlite --authorize
   ```

   备份必须由有权限的受控执行环境运行；源文件包含 WAL，**不可直接 `cp` 在线数据库**。新代码兼容旧 SQLite schema v1，但只访问 `wechat` 行；旧 `cms` 历史行不会触发任何 CMS 业务。保留旧源库用于对账，不删除。
7. `cd /opt/mp-publisher && docker compose build`。然后仅执行：

   ```bash
   docker compose run --rm mp-publisher check-wechat
   docker compose run --rm mp-publisher health
   docker compose run --rm mp-publisher status --profile producthunt
   docker compose run --rm mp-publisher dry-run --profile x
   docker compose run --rm mp-publisher dry-run --profile producthunt
   ```

   检查新状态与旧状态的各 profile 微信成功/异常数量一致；特别确认 `producthunt:2026-11-06` 是 `SUCCESS`。如当日内容未 READY，`dry-run` 返回 `SKIPPED`，不代表容器失败。`check-wechat` 仅验证白名单和凭据，不创建草稿。
8. 通过后用 `bash scripts/install-systemd.sh` 安装 timer（脚本**不会自动启用**），校验 `systemd-analyze verify` 与 `systemctl list-timers`；确认旧 timer 已禁用，再逐个启用 `mp-publisher-x.timer`、`mp-publisher-producthunt.timer`、`mp-publisher-trustmrr.timer`。
9. 等待下一轮自然定时产出，核对 `journalctl -u mp-publisher@<profile>.service`、微信 SQLite `SUCCESS` 与公众号后台草稿。**新链路首次实际写入验收后**再清理旧代码/安装；清理前不要删除旧 SQLite 与审计数据。

生产命令必须在硅谷服务器受控执行，不能将 `.env` 或 SQLite 上传 GitHub、Notion、Actions Artifact。

## 运维与回滚

- 暂停所有微信投递：禁用新三组 timer；必要时将受控环境中的 `WECHAT_ENABLE_DRAFTS=false`。不要停止 `cms-publisher` 的 GitHub Actions。
- 读取 `status/health` 不会调用微信 API；`check-wechat` 只调用 GET token；`preview/dry-run` 只读取 Notion。任何 `UNKNOWN/INTENT/FAILED` 先对账，不自动重试。
- 通过 `reconcile --channel wechat --result ABSENT --authorize --confirm-absent --evidence '...'` 重新允许一次**已核验不存在**的草稿。只用服务器受控权限执行。
- 回滚不得直接重新启用旧 timer：新数据库可能已有旧数据库不知道的成功投递，须先迁回新期微信状态或人工对账，否则可能重复建草稿。
- 不运行 `down -v`，不删除私有 SQLite，不公开映射端口。
