# OIN V01 Control Plane

Minimal account, Android device enrollment, and allowlisted device-control service for Board 0.

## Security contract

- Application accounts are separate from cloud-provider, Linux, and database accounts.
- Passwords use per-user PBKDF2-HMAC-SHA256 salts; opaque web/mobile session tokens are stored only as SHA-256 hashes.
- Web sessions use `Secure`, `HttpOnly`, `SameSite=Lax` cookies and CSRF tokens.
- A short mobile session is not a controller identity: it is limited to account `me`/logout, enrollment, and submitting/polling the exact candidate key for same-device credential repair. It cannot approve repair, revoke, authorize outbox reconciliation, list devices, or create/query tasks; those actions require a web controller session.
- Device enrollment requires a short mobile session plus proof of possession of an Android Keystore P-256 key. Legacy mobile revocation routes return `410 mobile_self_revoke_disabled`; only the authenticated account owner can revoke from the web device detail/API.
- Device ownership always comes from the authenticated server session; client-supplied owner/account fields are rejected.
- WSS authentication uses a fresh challenge and the registered device public key. Revoked credentials cannot reconnect.
- A socket object and a successful framework send-buffer call do not prove liveness. Task dispatch requires an open database session with a fresh heartbeat, recent valid client activity in the current Hub connection, and an exact match between that database `session_id` and the Hub connection's `session_id`. A stale or mismatched connection leaves the task `queued`; a session mismatch is not rewritten as device-offline evidence.
- Control-plane 0.4.2 remains deploy-first compatible with code25's exact signed full-payload auth/heartbeat and code26's distinct exact signed `transport_auth` plus minimal heartbeat. For code27 it additionally returns the exact `server_session_id` in `auth_ok`, so the phone's socket epoch and the server's durable session can be correlated. The auth signature domain still contains only `contract`, compile-time `app_version_code`, and `task_store_protocol`; code26 full-payload auth, code25 transport-auth, mixed fields, and unknown fields are rejected. Opening a code26+ transport session preserves the stored device profile as stale and changes only session/task-channel metadata; full device data can change only through `device_state_update`. Independent clients publish strict `task_channel_state` and `device_state_update` messages, begin task dispatch fail-closed, and open both database and current-Hub gates only after a current-session ready update. Not-ready tasks remain `queued`; device-state stale never makes a live transport offline.
- Task creation always targets the `device_id` in the owner-scoped route. Its JSON response separates `binding_state=bound`, current `realtime_channel_state`, durable `task_state`, and `dispatch_outcome`; an offline bound device is reported as `bound_offline_queued`, never as dispatched or as an inferred “latest” device.
- Known bounded client close reasons are retained on the device session (for example `client_reconnect:heartbeat_ack_grace_exhausted`). Unknown close text is discarded and only the close code is retained, so arbitrary client content cannot enter diagnostics.
- Only protocol-v1 `health`, `observe`, `xianyu_snapshot`, and the constrained `action` contract are accepted. Device/task ownership, parameters, expiry, and idempotency are server-enforced.
- Request/WebSocket entry points run a rate-limited cleanup of expired pre-auth, web/mobile session, enrollment/revocation challenge, and active task rows; task reads also materialize elapsed TTLs as `expired`.
- SQLite remains the isolated local-development/test database. Production may use PostgreSQL
  only through the dedicated `oin_v01` database, `oin_v01` schema, non-login owner
  `oin_v01_owner`, and least-privilege runtime role `oin_v01_app`; existing business
  databases, schemas, roles, and tables are outside the application boundary.

## Local test/start

```powershell
python -m pip install -r requirements.lock
$env:PYTHONPATH = (Resolve-Path .\src)
$env:OIN_DATABASE_PATH = (Join-Path (Resolve-Path .) 'runtime\oin-v01.db')
$env:OIN_PUBLIC_BASE_URL = 'https://example.invalid/oin'
python -m pytest
python -m oin_control_plane
```

The default public URL is deliberately non-routable. SQLite auto-migrates for local use.
Production PostgreSQL does not: apply the packaged `schema.postgresql.sql` out of process as
`oin_v01_owner`, grant only DML to `oin_v01_app`, and start the service with the root-readable
deployment environment. Startup validates schema version 2 and fails closed if it is absent.
The schema file intentionally does not mark itself ready: the production bootstrap writes the
version row only after its schema, append-only history grants, and role settings commit.

Relevant environment variables:

| Name | Purpose |
|---|---|
| `OIN_ENVIRONMENT` | `production` enables HTTPS/host hardening |
| `OIN_PUBLIC_BASE_URL` | Browser/API base URL, including an optional reverse-proxy prefix |
| `OIN_DATABASE_PATH` | Dedicated SQLite path for local development/tests; ignored when a PostgreSQL URL is set |
| `OIN_DATABASE_URL` | Secret PostgreSQL URL; production is pinned to role `oin_v01_app` and database `oin_v01` |
| `OIN_DATABASE_AUTO_MIGRATE` | Defaults true for SQLite and false for PostgreSQL; production PostgreSQL rejects true |
| `OIN_DATABASE_CONNECT_TIMEOUT_SECONDS` | PostgreSQL connect timeout; defaults to 3 seconds |
| `OIN_DATABASE_STATEMENT_TIMEOUT_MS` | Per-statement PostgreSQL timeout; defaults to 3000 ms |
| `OIN_DATABASE_LOCK_TIMEOUT_MS` | PostgreSQL lock-wait timeout; defaults to 1500 ms and cannot exceed the statement timeout |
| `OIN_INTERNAL_HTTP_ENABLED` | Defaults false; the only accepted production HTTP target is the temporary internal base `http://dominolif.com/oin` |
| `OIN_APK_PATH` | Exact APK served by `/download/apk` |
| `OIN_APK_VERSION` | Display/download version paired with the APK hash |
| `OIN_BIND_HOST` / `OIN_BIND_PORT` | Loopback listener behind Nginx; defaults to `127.0.0.1:18080` |
| `OIN_DISPATCH_FRESHNESS_SECONDS` | Maximum client-to-server inactivity allowed before task dispatch; defaults to 50 and must remain below the server's independent 90-second client-inbound silence timeout |

Do not place a real URL, credentials, IP address, token, database string, or `.env` file in the repository.

The PostgreSQL URL belongs only in `/etc/oin-v01/environment` (root-owned, mode `0600`), with
its password URL-encoded. The secret-free provisioning template is
`deploy/tx1-hx/postgresql-bootstrap.sql.template`.

## Three task sections (Android code44 / 0.13.0-v01)

Seller inventory and bound-device native inventory share `sections` (`publish`, `preheat`, `maintain`); builtins and recipes expose `section`. These are presentation categories, separate from execution `kind`. Existing builtin-derived recipes remain filtered. No new job, signing, account, WSS or scheduler contract is introduced. Web/native filter available workflows by section; an empty section cannot submit a new task, while a persisted uncertain submission retains its original idempotency identity for reconciliation. AI preparation and builtin buyer/preheat/full-selling paths remain pending real-page adaptation.

## EmployeeClient UI reuse (code45 / 0.14.0-v01)

TaskRow/StatusPill/ModalSheet and base.css tokens are adapted to OIN: task list first, settings in a native HTML dialog (bottom sheet on small screens), compact history rows, light underline tabs, material/authorization controls only when relevant. Existing hidden selection IDs, CSRF/auth, pending idempotency slot and endpoints are retained. No extra account-maintenance checks or new executor. Official web session list/group/modal/empty-state was inspected without submitting a job.

## Phone control UI (code46 / 0.15.0-v01)

Supersedes code45's list/modal presentation. Current execution shows server-derived name, current_step and real progress. Task, material and relevant confirmation are directly visible; history and advanced configuration are secondary. Native signed commands add scoped queued-only cancel with an exact job_id payload. Reconcile conditionally updates the previously read state, preventing a concurrent running claim from being overwritten. Existing web auth/CSRF, native identity, uncertain-submission idempotency and three sections remain. No running-pause endpoint or full business automation is implied.

## 每日任务控制与步骤呈现（2026-10-14）

schema9保存control_state/outcomes_json；暂停、继续、结束按任务锁协调，单步在途操作先收回执，未知写入结果不重放。inspect读取真实device_tasks回执；控制事件记录Web/绑定设备来源，manual恢复须confirm_manual:true。task_presentation.py生成阶段与人类可读动作。正常目录只暴露daily_preheat_v2/daily_maintenance_v2，取消现金签到/游戏任务，validate_steps拒绝已知奖励游戏按钮及旧v1启动。关闭可确认广告后重读，不将关闭算成原步骤完成；无文字结构广告尚待补齐。公开手机版本49/0.16.2-v01，详情上下浏览/低价购买新路径处于调研方案阶段，当前不是真实完整预热验收。

候选50（未部署）：正常目录切daily_preheat_v3/daily_maintenance_v3，wait可带browse_motion:true，在有效120秒浏览内约15秒派发一次下滑/回看，再观察；动作、暂停、剩余时间保存在同一步outcomes。普通导航/读取遇系统页或息屏暂停，断连后须显式resume，原输入法未知写入保护沿用。v2历史快照保留；v3目前仍首页浏览，不等于完整详情/订单自动化。

候选50商品指标：新增只读GET /api/v1/devices/{device_id}/seller/metrics及本机签名metrics命令。维护report的outcomes_json保存product_metrics及真实observe任务ID/回执时间，读200个本设备metrics任务做同标题同统计口径比较；今日曝光只比昨日近似同一时段，累计/未知周期不直接预警。product_metrics.py为未真机确认的保守语义解析，缺数据明示。实际有商品的自动擦亮仍待接通；未部署。

上述50候选源码已正式部署并由51手机包继承（2026-10-14）：正常任务目录v3最低code50，current app-update指向51。商品数据原生/Web只读链及广告/恢复逻辑已在生产运行包接线；指标缺失不填假值，真实有在售商品采集/自动擦亮尚未验收。Web按钮padding与间距修正已部署，未更新数据库schema9。

### 2026-10-14 商品实页修复（服务端 seller-20261014-published-goods）

- 支持闲鱼7.25.40合并语义的 `1\n在卖`，要求草稿/已下架同时唯一存在。
- 从商品卡片中读取真实标题、曝光、浏览、想要；今日数据账号汇总不作为商品。未明确统计周期的曝光不产生下降判断。
- 修复任务名称全角标点规范化导致report未写入指标的问题。
- 仅当完整在售页的今日数据面板明确含“今日已擦亮”时跳过原人工擦亮检查点；“试试超强擦亮”不表示免费按钮或已擦亮。不点击付费入口。免费可用按钮仍待实页测量。
- 高级工具支持一次调研/数据读取，沿用POST `/api/v1/devices/{device}/seller/jobs`的权限、流程校验和幂等提交；不开放commit流程快捷执行。
- 本次不替换51 / 0.17.1-v01 APK。

### 52 / 0.17.2-v01：全量商品维护（2026-10-14）

正常日常维护改用 `daily_maintenance_v4`：进入我发布的→按在售数量向下翻页采集全部商品→回我的→执行普通一键擦亮或略过已擦亮/无商品→返回首页正常浏览10分钟。旧v3任务快照保留，继续不会突然增加擦亮动作。

`scan_goods` 保存跨页去重、商品数、页指纹及来源observe任务编号；读满在售数量才完成，重复页/数量变化/同名商品无法区分时暂停，不能把第一页算全量。`brighten_all` 是显式的免费批量变更：仅接受完整卖家页面中唯一的“一键擦亮”或带曝光+数字的同节点，不匹配超强擦亮。已派发结果未知则冻结，不重放；已确认今日完成则跳过。付费/支付页只返回。可确认的广告关闭后重新读取。

新动作直接复用现有精确唯一节点点击和滚动通道；修正scroll_page方向语义，down向后浏览时手势向上。52客户端允许v4重试，最低API28。服务端沿用seller-native / builtin-jobs / jobs，无数据库迁移；自定义metrics含brighten_all仍要求authorize:true。版本构建与发布状态见本轮code52-release.md，测试夹具不能作为绑定手机完整流程验收。


### 2026-10-15 · 浏览恢复修复

seller.wait_remaining 对当前段时长和校验时间作范围限制；恢复时重置last_verified_at与last_moved_at，历史膨胀的remaining_seconds不会超过原段时长。完整页临时超时后在3秒后重读，最多3次；不将truncated快照当成动作证据。停止仍等待已在途动作的真实回执，未在途任务立即取消。新增旧校验时间与慢页重读回归测试，相关55项通过。真机接受状态见工作1000241。


## 当前预热 v5（2026-10-15）

58+ 手机目录只投影 daily_preheat_v5；旧任务快照不追加新行为。content_browse 以持久化阶段组合既有 observe/action：对应详情确认、内容停留/翻页、返回与去重；favorites=2 为显式收藏效果，内置开始授权，观察节点附带所属收藏按钮计数，+1才确认。结果未知暂停且不重放；原本已收藏导致-1时仅恢复原状态一次，再换一件。purchase_flow 读取真实搜索/详情/购买页，总价不超过10分才完成人工交接；永不产生提交订单/确认购买/支付动作。搜索有页数上限，失败明确提示。旧商品数据、维护和历史表保留，本阶段手机不展示入口。


### 手机双功能目录（63+）

63版以上投影daily_preheat_v5与daily_maintenance_v5。维护复用已测全商品采集/普通一键擦亮，再执行content_browse（600秒、两件收藏、低价购买交接），不再使用只在首页滑动的旧浏览计划。content_browse允许research和metrics，其他参数/权限约束不变；内置入口明确授权对应commit步骤。已存旧流程保持不可变。普通入口识别同一已核对个人页中的“一键擦亮”和“今日擦亮 曝光+数字”，排除付费入口。
搜索无候选或确认页超预算后，显式继续只重开有界搜索阶段：换一个关键词、退回已核对首页后搜索，保留已经完成的有效浏览和收藏，不重放业务动作。


64版维护修正：今日擦亮只作导航，不标记mutation；我发布的合并免费面板由手机tap_free_brighten实时核对后点免费区域，backend核对其转为试试超强擦亮，不点付费。旧未知任务显式cancel只结束后续执行，保留effect_outcome=unknown，不声称旧操作成功。

个人页擦亮完成后普通入口可能改成“发闲置 曝光+30”。此时不从变动横幅猜测状态，只点已测“我发布的”，在今日数据面板读取普通擦亮已完成状态，再接后续浏览。

购买确认页实测补充：按钮可能为“免密支付 总价，按钮, 免密支付\n¥总价”，只读解析并核对两处金额一致。已测标价0.10但运费5.60/5.00的候选，分别实际5.70/5.10，均关闭页面退回结果继续筛选，不点支付。超预算拒绝只关闭页面，不依赖商品标题；可交给人工的预算内候选仍须商品身份和总价共同确认。浏览601有效秒/9件/两次收藏已真机完成；预算内最终交接是否完成以阶段报告为准。


### 商品阅读节奏（65版后服务端调整）

阅读空档每段随机5–10秒，计划中的间隔和方向保存到当前商品状态，观察/暂停/重启不重新抽取同一待执行计划。下滑、有限回看、继续下滑在商品详情内组合；逻辑滚动深度限定0–3，首段向下，到上/下边界时约束方向。只从已确认可读、可见且未看过的卡片中随机选择下一件。
每件原25–65秒内容目标、任务600秒有效累计、至少3件合格详情、两次收藏确认、卖家擦亮和预算内人工购买交接均保持。收藏/下单/支付/付费推广不参与随机组合。滚动成功回执后才计scrolls；失败暂停，不能把发出意图当已完成。详情等待下一次读取缩到2秒以减少调度拖尾；实际静止时间另受手机响应和通道延迟影响，不承诺每次严格落在5–10墙钟秒。

最新纠正与实现（2026-10-15）：50个关键词用于前段日常搜索浏览（生活、文具、家居等），不用于统一筛低价；每看完两件切换一个不同搜索词，搜索结果进入详情，沿用5–10秒随机停留/上下滑动、两次收藏、防重复和暂停续接。没有可确认卡片时最多翻三屏后换词，全局翻页上限保留。新建v5预热与日常维护启用search_browse；旧任务保留已存流程，不中途重置。
收尾单独使用虚拟商品/服务词库，按价格找1–10分候选；预算仅在收尾生效。最终仍核对商品身份、原生确认页及一致的实际应付总额，虚拟商品无需强制存在运费行，但不能把没有运费行视为总价已知。超预算关闭继续找；符合预算则停在确认页，程序不提交/付款，也不主动跳回OIN。无法找到时诚实暂停，不报告成功。不承诺全站最低或所有虚拟商品实际可按引流标价成交。
