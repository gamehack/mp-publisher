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

新增 `systemd/mp-publisher-overseas.{service,timer}`，每天 **北京时间 07:30** 通过现有硅谷服务器 Docker OneShot 扫描 Notion「07 · 海外创业志」的连续期号。微信端只在构建 `overseas` 草稿 HTML 时移除末尾「资料来源」章节；Notion 原文、来源链接、档案编号和 READY SHA-256 均完整保留，其他 Profile 不受影响。`run --profile overseas --auto` 依据持久化 SQLite 台账跳过 `SUCCESS`，每次最多发送下一期已签署 READY 的草稿；未 READY / 缺期号 / 不明确回执则停止，绝不自动越过或重试可能成功的微信调用。该时段与既有 X timer 相同，两个栏目各自使用独立锁与来源键；首次自然执行需核对资源及微信 API 结果。不会公开发布、群发或修改原先的 X/产品猎人/TrustMRR timer。

继续复用服务器的稳定出口 IP、既有密钥、封面与 `/var/lib/mp-publisher/state.sqlite`。上线时由既有部署流程安装 unit，并在核实环境变量 `PUBLISH_OVERSEAS_ENABLED=true`、`WECHAT_OVERSEAS_ENABLED=true`、`WECHAT_ENABLE_DRAFTS=true` 以及第 01 期台账已经 `SUCCESS` 后，单独启用：

```bash
sudo /opt/mp-publisher/scripts/install-systemd.sh
sudo systemctl enable --now mp-publisher-overseas.timer
```

新 timer **不会**因为代码合并或安装 unit 而自动启用。人工 `--period NN` 仍可应急使用，和定时器共用 SQLite 防重台账。

## 五专栏封面池 V1（可逐栏目启用，2026-11-09）

微信图文草稿仍为每篇 **一个永久素材 `thumb_media_id`**，可在同一篇中另行传 `pic_crop_235_1`、`pic_crop_1_1`（横图与方图裁切）。这不是图片消息 `newspic` 的 20 图能力。

现行老配置 `WECHAT_THUMB_MEDIA_ID_X`、`WECHAT_THUMB_MEDIA_ID_PRODUCTHUNT`、`WECHAT_THUMB_MEDIA_ID_TRUSTMRR`、`WECHAT_THUMB_MEDIA_ID_OVERSEAS` 保留。**不配置 `WECHAT_COVER_POOL_JSON` 时，行为和过去完全一致**；即使只配置某个专栏的素材池，其余栏目继续使用旧封面，不会发生突变。

图片先在受控服务器通过微信永久素材接口上传，获得每张独立的 `media_id`，然后只在受限权限的服务器 `/etc/mp-publisher/publisher.env` 增加一行私有 JSON（以下仅为占位符，不能直接用于生产）：

```dotenv
WECHAT_COVER_POOL_JSON='{"overseas":["OS_ID_1","OS_ID_2","OS_ID_3"],"x":["XI_ID_1","XI_ID_2","XI_ID_3"],"producthunt":["PH_ID_1","PH_ID_2","PH_ID_3"],"trustmrr_weekly":["TW_ID_1","TW_ID_2","TW_ID_3"],"trustmrr_monthly":["TM_ID_1","TM_ID_2","TM_ID_3"]}'
```

15 张素材编号：
- 海外创业志：OS-01~03（创业故事）
- X 商业情报：XI-01~03（商业信号）
- 产品猎人日报：PH-01~03（新品发现）
- TrustMRR 周报：TW-01~03（增长周榜）
- TrustMRR 月报：TM-01~03（增长月报）

`source_key` 经 SHA-256 固定映射到三张素材之一，相同文章重试仍然选同一张。TrustMRR 根据 `weekly:`/`monthly:` 选择不同池。启用素材池后，会发给草稿接口 `pic_crop_235_1=0_0_1_1` 和 `pic_crop_1_1=0.2872_0_0.7128_1`；裁剪参数必须先在公众号实际草稿中验证，再逐个开启池。**media_id 不得提交 GitHub、写入日志或 JSON 测试快照。**

原创的 15 张 JPEG、SHA-256 文件清单及**默认 dry-run 的永久素材上传脚本**已版本化打包在 `assets/cover-pool-v1-deploy.zip`，公开清单在 `assets/cover-pool-v1-manifest.json`。部署前先用 `unzip -t` 和 `upload_to_wechat.py --pack <解压路径>` 只读验包，确认图片均符合 JPEG、大小和 SHA-256 限制。由受控硅谷主机显式 `--authorize-upload` 上传微信永久素材，每成功一张就写私有 0600 检查点；上传成功后生成独立 0600 的 `cover-pool.env.fragment`，**不自动合并生产环境配置**，需确认完整 15 张及微信裁剪兼容性后再启用。

素材 ID 和上传检查点不能提交仓库。旧草稿、SQLite 去重状态和 timer 不变，不运行 `freepublish` 或群发。

## 封面 V2：手机小图可读性试验（2026-11-11）

上一版 15 张封面中央有较大的栏目海报文字，在微信手机端 1:1 小图里难以阅读，也容易和微信在横图底部自动叠加的文章标题重复。V2 **不是**在原图上单纯放大文字，而是重新生成五个视觉场景池，每池 3 张、共 15 张（`assets/cover-pool-v2/covers/`）：

| 栏目池 | 场景顺序（与私有 `WECHAT_COVER_POOL_JSON` 数组对应） |
|---|---|
| `overseas` | 深夜独立开发者 / 居家工作室 / 咖啡馆远程创作者 |
| `x` | 全球商业信号 / AI 社交网络 / 跨境创业网络 |
| `producthunt` | 移动端新品 / 开发者软件 / 生产力工具 |
| `trustmrr_weekly` | 快速增长 / 排行榜 / MRR 看板 |
| `trustmrr_monthly` | 月度趋势 / 月度进展 / 经营看板 |

每张 JPEG 900×383，体积低于 64 KiB。去掉了中央玻璃面板和多行文字，中心保留易于识别的单一主体，底部为微信自动标题留出空间。清单 `assets/cover-pool-v2/manifest.json` 含每张图片的 SHA-256；人工已按 2.35:1 横图和约 100×100 的中心方图分别审阅。它们是原创生成的示意性栏目场景，不是某位真实创业者或某款产品的新闻照片，不应标成纪实人物肖像。

### 本地只读验收

```bash
python scripts/upload_cover_pool_v2.py --pack assets/cover-pool-v2
python -m pytest tests/test_cover_assets_v2.py tests/test_covers.py tests/test_cover_quality.py -q
```

上传工具默认只验包，不访问微信；检查文件名、JPEG 尺寸、大小、SHA-256、栏目和 15 张完整性。生产服务器应使用已有 Docker Compose 镜像、私有 `env_file` 和 `/data` 卷运行上传器，避免在宿主机临时导出微信密钥：

```bash
cd /opt/mp-publisher
sudo docker compose run --rm --no-deps --volume /opt/mp-publisher:/workspace:ro \
  --entrypoint python mp-publisher /workspace/scripts/upload_cover_pool_v2.py \
  --pack /workspace/assets/cover-pool-v2 --state /data/cover-media-v2-uploads.json

# 只在确认微信白名单、私有凭据和离线验包通过后执行一次；不建草稿
sudo docker compose run --rm --no-deps --volume /opt/mp-publisher:/workspace:ro \
  --entrypoint python mp-publisher /workspace/scripts/upload_cover_pool_v2.py \
  --pack /workspace/assets/cover-pool-v2 --state /data/cover-media-v2-uploads.json \
  --authorize-upload
```

上传器只生成私有 `WECHAT_COVER_POOL_JSON` 配置片段，**不会自动改生产 `.env`，不会启 timer**。上传失败或不确定时会写 `UNKNOWN` 检查点并停止，不能无脑重试。

15 张素材均返回 `UPLOADS_CONFIRMED` 后，使用无需额外 Python 依赖的安全激活脚本。它在进程内核对私有上传检查点、素材清单及环境片段，不打印凭据或 `media_id`，默认只读；授权后先创建一个 root-only 备份，再原子更新旧环境文件中的封面池和语义路由设置，保留其他键：

```bash
cd /opt/mp-publisher
sudo python3 scripts/activate_cover_pool_v2.py
# 必须先确认 DRY_RUN_OK，再显式授权：
sudo python3 scripts/activate_cover_pool_v2.py --authorize-activation
# 预期：ACTIVATED, pool_count=5, materials=15, routing=semantic
sudo docker compose run --rm --no-deps mp-publisher health
```

生产定时任务每次启动新的 Compose one-shot 容器，会自动读取更新后的 `/etc/mp-publisher/publisher.env`，不用重启 systemd timer。回滚时将该目录中新生成的 `publisher.env.pre-cover-v2-*.bak` 中**本次对应**的备份恢复成原私有配置；先确认文件权限、目标与备份关联，不清理 SQLite 或历史草稿。`ALREADY_ACTIVE` 表示重复激活时未产生第二份备份。

### 新增两种可回滚的路由配置

- 不设置 `WECHAT_COVER_ROUTING` 或设为 `hash`：保留按 `source_key` SHA-256 选 3 张之一的既有逻辑。此时旧封面池和旧栏目配置行为不变。
- `WECHAT_COVER_ROUTING=semantic`：依据文章标题与摘要中的栏目特定词选择一个场景。无明显关键词或得分并列时仍按 `source_key` 稳定哈希回退。不调用付费模型，不随日期随机变化。
- 可选 `WECHAT_COVER_OVERRIDES_JSON='{"overseas-stories:05":"PRIVATE_MEDIA_ID"}'`：对某篇文章按完整来源键使用已经上传的专属永久素材 ID，优先于封面池。专属图片也必须是 **900×383 中心安全构图**，因为继续使用 V2 同一组横图/方图裁剪参数。只允许写在私有生产环境，不能提交到 GitHub。

先逐栏目启用新的 `WECHAT_COVER_POOL_JSON`，核对在公众号草稿中横图和小图都显示正常，再考虑开启 `semantic`。生产发布仍由原来 systemd timer 驱动，`draft/add` 行为与幂等 SQLite 不变。已经 SUCCESS 的文章不会因封面变更重新投递。

### 建议观察 3～5 天并保留回滚条件

1. 每日检查新增公众号草稿的大图及 1:1 小图：能否识别主体、白色自动标题是否清楚、栏目是否区分明显。
2. 对比不同期号是否发生同一张图重复过密；语义不匹配记录为选图问题，不要立即重投草稿。
3. 检查 `mp-publisher health`、各 profile 状态以及 `UNKNOWN`，保证新功能没有新增失败投递。
4. 如果图文封面有异常，先恢复旧的私有 `WECHAT_COVER_POOL_JSON` 值，并把 `WECHAT_COVER_ROUTING=hash`，随后做离线 `dry-run` 与微信草稿人工核对。

**边界：**V2 静态场景池可以增强文章与画面的相关度，但不能保证每期都出现文章中的真实人物/产品。后续若需要逐篇 AI 生成，需要独立的图像生产步骤先产出并审核素材，再把每期的永久素材 ID 安全地加入精确覆盖表；不要在 `draft/add` 的幂等写入过程里临时调用图像服务。
