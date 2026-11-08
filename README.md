# mp-publisher

潜界 AI 的独立微信公众号**草稿箱投递器**。内容生产项目将正式文章写入 Notion READY 专栏；本项目只从固定专栏读取原稿、校验来源与正文哈希、转换微信 HTML，再通过微信公众平台 `draft/add` 创建**未群发草稿**。

**运行位置：硅谷服务器 Docker 容器**。微信公众平台的 API IP 白名单包含该服务器的出口 IP，因此定时发布不能在 GitHub-hosted Actions 上执行。本仓库的 Actions **只跑 CI**，生产调度由服务器 systemd timer 启动临时容器，不运行常驻 HTTP 服务或定时轮询。

配套系统：独立 [cms-publisher](https://github.com/gamehack/cms-publisher) 负责 CMS 草稿（GitHub Actions），二者互不调用。旧统一发布器的服务器代码与全部 systemd 单元已于 2026-11-08 下线清理。本项目没有 CMS 客户端、CMS 密钥、CMS 草稿写入或公众号群发/公开发布能力。

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

### 现行部署与迁移验收（2026-11-08）

- `/opt/mp-publisher` 已独立运行，环境文件位于 `/etc/mp-publisher/publisher.env`，状态位于 `/var/lib/mp-publisher/state.sqlite`；权限由 root 管理。
- 已由旧 SQLite 的一致性在线备份迁入微信幂等状态，迁移核对 16 条微信 SUCCESS。旧 CMS 行不会被本项目消费；不可重置已成功记录。
- 三个新 timer 已启用：`mp-publisher-x.timer`、`mp-publisher-producthunt.timer`、`mp-publisher-trustmrr.timer`。旧服务器代码、旧 .env、四个 systemd 单元和旧锁已清除。**不要按早期迁移步骤寻找 /opt/qjai-text-publisher/.env 或重新安装旧单元。**
- 为核对尚未解决的旧 CMS PENDING 记录，暂保留 `/var/lib/qjai-text-publisher` 只读历史账本；本项目不依赖该路径。待 CMS 对账后再清理。
- 首次自然微信调度仍待验收：X 2026-11-08 07:30；Product Hunt 17:30/18:30；TrustMRR 2026-11-09 03:00（北京时间）。通过容器日志、SQLite 状态及公众号后台草稿核实，不得因定时器启用就声称已投递。
- 安全只读检查：`cd /opt/mp-publisher && sudo docker compose run --rm --no-deps mp-publisher health`；`check-wechat` 仅 GET token，不建草稿。

生产命令必须在硅谷服务器受控执行，不能将 `.env` 或 SQLite 上传 GitHub、Notion、Actions Artifact。

## 运维与回滚

- 暂停所有微信投递：禁用新三组 timer；必要时将受控环境中的 `WECHAT_ENABLE_DRAFTS=false`。不要停止 `cms-publisher` 的 GitHub Actions。
- 读取 `status/health` 不会调用微信 API；`check-wechat` 只调用 GET token；`preview/dry-run` 只读取 Notion。任何 `UNKNOWN/INTENT/FAILED` 先对账，不自动重试。
- 通过 `reconcile --channel wechat --result ABSENT --authorize --confirm-absent --evidence '...'` 重新允许一次**已核验不存在**的草稿。只用服务器受控权限执行。
- 回滚不得重新创建旧 timer：旧部署已清理，且新数据库可能已有旧账本不知道的成功投递；必须先人工对账并制定独立回滚方案，否则可能重复建草稿。
- 不运行 `down -v`，不删除私有 SQLite，不公开映射端口。

## 海外创业志（M7 首期手动接入的历史说明）

- 新增 `overseas` Profile，固定读取 Notion「07 · 海外创业志」的直接子页面，标题格式 `海外创业志 01|<人物故事标题>`，来源键 `overseas-stories:01`。序号与日期无关。
- 与其他日更 Profile 的日期前缀不同，海外创业志的栏目名和期号会**保留在公众号实际草稿标题中**；完整标题按 64 UTF-8 字节上限验收，不静默截断，不删除序号。编辑可先精简 Notion 标题与正文一级标题，再重算 READY 摘要。
- M7 期间 `preview/dry-run/run/backfill` 均需显式 `--issue 01`（兼容 `--period 01`）；M8 才新增单独的自动扫描 timer，不影响原有三个定时任务。
- `PUBLISH_OVERSEAS_ENABLED=false`、`WECHAT_OVERSEAS_ENABLED=false` 为默认值；封面素材 `WECHAT_THUMB_MEDIA_ID_OVERSEAS` 需要在受控服务器环境中配置，不能写入仓库。
- 专栏封面图位于 [`assets/overseas-stories-cover-v1.jpg`](assets/overseas-stories-cover-v1.jpg)（900×384，低于 64 KiB）。这是公开静态品牌素材，**不是**微信素材 media_id；需由受控环境先上传为公众号永久封面素材，再将返回的 ID 写入服务器私有配置。仓库与日志不能保存返回 ID 或微信凭据。
- 首篇 M7 人工审核通过、正文 READY 校验、公众号封面与幂等状态核对完成后，才允许授权开启新 Profile。仅投草稿，不群发。
- 规范见 [M7 编辑与自动化验收](https://app.notion.com/p/4119e605ccf881f0ae22dcad97535908)。
# 海外创业志 M8 自动草稿投递

新增 `systemd/mp-publisher-overseas.{service,timer}`，每天 **北京时间 09:15** 通过现有硅谷服务器 Docker OneShot 扫描 Notion「07 · 海外创业志」的连续期号。`run --profile overseas --auto` 依据持久化 SQLite 台账跳过 `SUCCESS`，每次最多发送下一期已签署 READY 的草稿；未 READY / 缺期号 / 不明确回执则停止，绝不自动越过或重试可能成功的微信调用。不会公开发布、群发或修改原先的 X/产品猎人/TrustMRR timer。

继续复用服务器的稳定出口 IP、既有密钥、封面与 `/var/lib/mp-publisher/state.sqlite`。上线时由既有部署流程安装 unit，并在核实环境变量 `PUBLISH_OVERSEAS_ENABLED=true`、`WECHAT_OVERSEAS_ENABLED=true`、`WECHAT_ENABLE_DRAFTS=true` 以及第 01 期台账已经 `SUCCESS` 后，单独启用：

```bash
sudo /opt/mp-publisher/scripts/install-systemd.sh
sudo systemctl enable --now mp-publisher-overseas.timer
```

新 timer **不会**因为代码合并或安装 unit 而自动启用。人工 `--period NN` 仍可应急使用，和定时器共用 SQLite 防重台账。
