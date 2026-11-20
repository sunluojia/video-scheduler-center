# TikTok 达人资料与作品获取方案

> 2026-11-19 起由[爬取方案](crawler-design.md)取代：浏览器改用 patchright + 本机 Chrome 并复用身份目录，首期不接 DTK。第 3 节的选片和失败判定已并入爬取方案第 7 节。本文保留作 DTK 对测参考。

先用 **Camoufox + Playwright** 接入调度中心，同时用 **DTK API** 验证能否更快地取得同一份结果。两条路线都接收 `crawl_creator`，输入为达人账号 `handle`、批次截止时间 `cutoff` 和作品数 `max_works`（默认 5）。输出为核实过的达人资料及截止时点的最新作品。图文和视频共同占位，调度中心锁定清单后只给视频派发下载任务。

## 1. Camoufox + Playwright

1. 采集执行器按设备启动并复用 Camoufox 的 Firefox 进程。每次尝试先申请 `crawler.crawl_creator`（获取信息）线路，再新建独立的 Playwright 浏览器上下文。启动上下文时绑定所分配的线路，整次尝试沿用。完成、失败或取消时关闭上下文并归还租约。[Playwright 上下文代理](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-proxy)
2. 打开公开达人主页前开始监听页面响应。用主页资料和页面数据核对目标账号，取得主页链接、昵称和平台用户 ID。昵称、用户 ID 可缺省，前提是账号身份已确认。
3. 读取页面实际加载的作品列表，核对响应状态、业务状态、列表和分页字段。继续页由页面正常滚动加载，一次只推进一页。保留页面脚本和列表请求，省去视频媒体流。到达末页或满足选片终止条件时结算。[Playwright 请求事件](https://playwright.dev/python/docs/api/class-request)
4. 采集执行器独立锁定 Camoufox 浏览器、Python 包和 Playwright 的验收版本。Camoufox 上游 Python 包当前声明 Playwright `<1.63`，旧项目锁的是 `1.64.0`。[Camoufox 依赖声明](https://github.com/daijro/camoufox/blob/main/python/pyproject.toml) · [旧项目依赖](../../video-insight-pipeline/pyproject.toml)

浏览器试跑初值为每台设备 **2 个并行上下文、单次 60 秒、最多 10 个有效列表页**。没有可用线路时按调度中心给出的时间重新排队，等待期间释放执行名额。代理连接或认证失败按实际原因归还租约。偶发超时、断连或 `5xx` 可退避后重试一次。

## 2. DTK API

旧项目使用的是 **Evil0ctal/Douyin_TikTok_Download_API**。本方案按 **v5.1.11** 的接口核对，由采集执行器请求内网自托管 DTK，只给调用端 `tiktok:read` 权限，出网由 DTK Worker 自己管理。浏览器继续使用调度中心的采集线路租约。[DTK 项目](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) · [v5.1.11 发布](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.1.11)

| 步骤 | API | 如何使用 |
| --- | --- | --- |
| 核对达人 | `GET /api/v1/tiktok/user?url=<达人主页>` | 核对 `unique_id` 与目标账号。取 `nickname`、`web_url` 和数字字符串 `uid`。`uid` 映射到 `platform_user_id`，另取 `sec_uid` 给作品接口使用，缺失时记录资料不全。 |
| 读取作品 | `GET /api/v1/tiktok/user/posts?sec_user_id=<sec_uid>&count=20` | 从第一页开始，读 `items`。有下一页时原样传回上一页的 `cursor`。核对每条作者，将 `content_id`、`web_url`、`created_at`、`kind`、`description`、`tags` 转为调度中心的作品字段。将 `image_album` 映射为 `photo`，话题取名称，缺省描述和话题分别用空字符串和空列表。 |

[DTK 路由实现](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.11/src/dtk/api/routes/content.py#L754) · [达人及作品数据模型](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.11/src/dtk/models/content.py#L97) · [TikTok 列表解析](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.11/src/dtk/platforms/tiktok/parser.py#L508)

- 接收 `202` 后，按本地 `task_id + run` 和请求步骤持久化 DTK 任务 ID，再轮询 `GET /api/v1/tasks/{task_id}`。轮询返回 `done` 时读 `data.data`，返回 `failed` 时读 `data.error`，即使查询任务本身返回 HTTP 200。重启后接续原任务。已受理或提交结果未知时，保留当前步骤状态，避免重复提交。取消后停止等待，迟到结果不进入派单。[DTK API 异步说明](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.11/documents/zh/11-api.md#异步模型)
- API 试跑先按 **20 秒、最多 10 个有效列表页**计，从资料请求开始，包含 DTK 排队、轮询和分页。v5.1.11 的常规作品模型未提供可用于判定作品置顶的字段，试跑以核实 `has_more=false` 的末页为完成条件。带 `guest_fallback` 等不完整信号的列表，以及取数时间早于 `cutoff` 的缓存页，都不足以确认最新作品。后续若能用实测证据确认顺序和置顶规则，再验证能否提前停止。[作品模型](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.11/src/dtk/models/content.py#L143)
- 旧项目 2026-11-04 对 v5.1.10 的实测中，两位达人资料各约 **4.04 秒**。一位拿到 22 条的列表响应，耗时 **2.04 秒**。另一位的资料成功，但列表连续返回 `NOT_FOUND`。本轮确认了 v5.1.11 的接口，部署线路上的成功率与端到端耗时待对测。[旧版实测记录](../../video-insight-pipeline/docs/dtk-5110-real-test-2026-11-04.md#现有匿名游客对照)

## 3. 统一选片和失败判定

- 按平台作品 ID 去重，ID 按字符串保存。发布时间换算为 UTC，`published_at <= cutoff` 才入选。按发布时间倒序，同一秒按作品 ID 的数字值倒序。置顶按原发布时间排序，图文和视频一起取前五条。
- 看到了目标列表的有效末页即可结算。若某一路已验证普通作品全局倒序及置顶规则，且本轮页间连续、没有乱序，在读到严格早于第 5 名的普通作品后也可结算。同秒边界继续读取。重复游标、缺页、上限耗尽或可能改变排行的作者、时间、类型无法核实，本轮回 `failed`。
- 明确核实账号私密或不存在时回 `private`、`not_found`。资料已核实但作品接口报 `NOT_FOUND`，记录为作品列表未确认。只有证据表明公开账号在截点及以前没有作品时，才回 `ok` 和空清单。空正文、访问受限、资料与列表冲突都回具体失败原因。
- 遇到限流，按 `Retry-After` 或 DTK 的 `retry_after` 等待，缺省 60 秒。浏览器的线路租约按 `throttled` 归还。登录要求、`403`、验证页等访问限制结束本轮。这类信号优先于任何路线切换。[Retry-After 规范](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3)

## 4. 执行顺序与验收

1. **首期默认浏览器。** 用同一批获准处理的公开达人分别跑浏览器和 API，仅浏览器作为正式任务的结果来源。收集至少 10 个账号，覆盖图文混排、置顶、需要翻页和确认无作品。用同一 `cutoff`，逐条核对作品 ID、类型、顺位和发布时间。验证两个并行浏览器上下文都走各自分配的出口。比较完整清单成功率和从开工到清单核实的耗时，API 的排队、翻页和失败都计入，缓存命中单独统计。
2. **验收后再启用 API 优先。** 对测清单一致、完整成功率达到浏览器水平且耗时有改善，再把 API 设为默认。API 首次连接已明确失败且未提交，或 DTK 任务已结束且只卡在数据格式不兼容时，可切到浏览器一次。未结束的 DTK 任务继续按原 ID 查到期限。切换沿用原 `task_id`、`run`、`cutoff`，结果来自一次独立核实的清单。首试、领线等待和重试共用 5 分钟预算。
3. **共用调度接口。** 受理后发 `accepted`，本地保存唯一最终结果和待发事件后回 `crawled`，补发沿用 `event_id`。沿用现有作品字段 `position`、`work_id`、`url`、`type`、`published_at`、`description`、`topics`。可选 `profile`（`handle`、`url`、`nickname`、`platform_user_id`）需要在调度中心补充接收、入库和详情返回。名单里的 `oecuid` 保持原值。[现有事件结构](../backend/src/scheduler_center/modules/crawl/messages.py) · [派单逻辑](../backend/src/scheduler_center/modules/flow/service.py)

验收追加截止后作品、同秒和跨页重复、上游限流、清单不完整、DTK 查询恢复、重启后事件补发。核对调度中心锁定同一份清单并按视频逐条派单。

[爬虫总方案](crawler-design.md) · [代理分配](proxy-design.md) · [单视频下载方案](video-download-design.md)
