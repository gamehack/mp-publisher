# 微信公众号封面 V2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有公众号草稿投递器中增加可回滚的内容相关封面路由、质量校验及轻文字 V2 素材，支持未来单篇独立封面。

**Architecture:** 在 `covers.py` 保留旧哈希选择作为默认行为，新增显式 V2 路由和按 `source_key` 覆盖。以单独的质量校验模块和数据清单验证裁剪与素材，再经原有 `runner` 和 `channels` 进入微信草稿 API。

**Tech Stack:** Python 3.11、pytest、GitHub CI、JPEG、微信草稿箱 API。

**Spec:** `docs/superpowers/specs/2026-11-11-wechat-cover-v2-design.md`

## Global Constraints

- 默认行为与原有 MediaID 池完全一致；V2 显式启用，支持回滚。
- 绝不向日志、Git、测试快照暴露私有 MediaID。
- 微信投递仅 `draft/add`，失败维持 UNKNOWN，不重试。
- 生产图源同一素材支持 2.35:1、1:1 裁剪。

## Review Focus

- 只有一个栏目的 V2 配置时其他栏目仍走原路径。
- 语义不匹配时必须确定性回退，而不是随机选择。
- malformed JSON 和重叠 source_key 不能泄露 media id。
- 不合法裁剪必须在调用微信前阻断。
- 已成功投递的文章不能因新封面再次创建草稿。

---

### Task 1: 内容相关选择与精准覆盖

**Files:** `src/mp_publisher/covers.py`、`tests/test_covers.py`、`src/mp_publisher/runner.py`

- [ ] 写失败测试：默认哈希兼容、明确开启语义、关键词分类、无法匹配回退、精确覆盖优先、非法 JSON 失败不泄露。
- [ ] 运行测试，确认在新功能缺失处失败。
- [ ] 实现轻量语义选择与来源键覆盖，保留现有接口。
- [ ] 跑目标测试与全套测试；再提交。

### Task 2: 裁剪与素材质量校验

**Files:** `src/mp_publisher/cover_quality.py`、`src/mp_publisher/channels.py`、`tests/test_cover_quality.py`

- [ ] 写失败测试：坐标边界、非法矩形、比例偏差、正常宽/方图、多余文字风险以素材验包约束实现。
- [ ] 运行测试确认红灯。
- [ ] 在请求微信前验证所有非空裁剪；新增独立 JPEG 素材验收脚本与机读清单。
- [ ] 全套测试与离线验包通过，提交。

### Task 3: V2 封面素材与部署说明

**Files:** `assets/`、`README.md`、`tests/test_cover_assets_v2.py`

- [ ] 生成五栏目 V2 视觉素材（每栏目三种场景），全部无中央文字面板。
- [ ] 验证图片体积、宽方裁剪可读性、场景标签与 JPEG，写失败/成功验收测试。
- [ ] 更新私有上传与逐栏目启用指引，不写入任何永久素材 ID。
- [ ] 跑全套测试，提交，并在可用的生产管理链路中安全部署/验证；无法访问时报告明确阻碍。
