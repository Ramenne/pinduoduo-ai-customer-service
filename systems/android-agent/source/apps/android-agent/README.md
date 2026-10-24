# OIN V01 Android Agent

Current release: **0.15.0-v01 / versionCode 46**. OIN is a phone controller: the native task center shows the current task name, state, actual step and progress first, then visible section/task/material controls and a direct start action. History is secondary. This supersedes code45's EmployeeClient list/modal adaptation after the user's product-positioning correction. Reuse mature state/error/update handling, not another product's navigation. Native active-task progress refreshes every five seconds while the Activity is visible. A queued job can be cancelled through the signed bound-device command; running jobs cannot. Unfinished jobs block a new start, while uncertain submissions retain their original identity for recovery. Minimum Android **9 (API28)** includes Android10/11. API29/35 private-Activity launch, three section switches and finish passed for46; target-phone authenticated execution and actual Xianyu description entry remain unverified.

## Current description input candidate: code41 / 0.12.3-v01

The phone now has a native task center (no WebView or repeated web login), system-provided safe areas, and an opt-in `OIN 闲鱼填写` input method. The owner must enable and choose it through Android settings; OIN never changes the default IME or accepts a system security prompt automatically. Its small keyboard includes an input-method switch button for returning to a normal keyboard.

`set_description_text` takes exactly `{action,package,value}` with the Xianyu package and 1–5000 UTF-16 characters. It has no arbitrary selector, package, URL, clipboard, shell, broadcast receiver or exported command API. Android protects the IME service with `BIND_INPUT_METHOD`. MainActivity shows enable/select status and opens the official system screens. Other applications, password/numeric/single-line editors, a locked phone, a paused agent, missing/partial/oversized extraction, and a missing publish-description page are rejected before writing. No editor contents are logged or learned.

The service runs on its main looper with a bounded task deadline and an atomic cancellation/write fence. Cancellation wins before a write permit; a write that already started is retained as potentially unknown. It requires the current Xianyu multiline InputConnection, visible unique `发布, 发布` + `完成` anchors, and the known empty description placeholder. Existing different text is preserved. Into an empty editor it calls `commitText`, then verifies that complete extracted text matches the requested text exactly; matching existing text is a readback-only idempotent success. A changed connection/window, timeout or unmatched readback is never success. Cloud workflows retain potentially written-but-unverified outcomes as `unknown`, stopping later jobs; an inactive IME is a known no-write failure.

The built-in `prepare_description_v1` consumes the chosen saved item's real description, opens the publish form, focuses the description, invokes this action, closes the editor and checks the form again. It prepares a draft, never publishes a listing or edits an existing listing. Cloud admission requires code41 and an owner/device-scoped item; older phones see the preparation unavailable. Policy tests (25 checks), legacy contract/bounded tests and Android Release compilation passed. These do not prove physical InputConnection behavior; installation, IME selection, actual text round-trip and identity/safe-area acceptance remain pending on the target phone.

Update entry: `https://htu.philosophy.dwshare.online/oin/download/apk`; use the application's update button or cover-install without uninstalling. Public version and signing identity are checked separately in the deployment receipt.

This directory is an isolated evolution of the accepted PhoneBridge V2.1 input baseline. The original `C:\Users\developer\Desktop\oin\02-源码与基准\01-待审代码-PhoneBridge-V2.1` tree is not modified.

## Preserved executor

- `health` uses `BridgeState.status()`.
- `observe` calls `BridgeAccessibilityService.observe()` directly.
- `xianyu_snapshot` uses `XianyuSnapshotCapture` and the real `MessagingPlatformRegistry` entry `xianyu.android.accessibility`; the existing result fields remain compatible and now carry `oin.messaging.snapshot` v1 metadata.
- The cloud protocol exposes only the versioned constrained `action` allowlist (launch Xianyu, exact visible-node click, semantic scroll, back/home, and code35 bounded node text/unique click/image import). It does not expose arbitrary packages, coordinates, unrestricted text input or Shell. Legacy `click_node` and pure-Java validator overloads are unchanged.

## Code35 bounded selling/maintenance actions (candidate, not device-accepted)

Version: code35 / `0.11.0-v01`. All three actions require `package: "com.taobao.idlefish"` and exact field sets; unknown fields are rejected in both cloud admission and dispatch.

Changed paths in this Android slice (relative to `apps/android-agent/`):

```text
README.md
app/build.gradle
app/src/main/AndroidManifest.xml
app/src/main/java/com/feifei/phonebridge/ActionDispatcher.java
app/src/main/java/com/feifei/phonebridge/BridgeAccessibilityService.java
app/src/main/java/com/feifei/phonebridge/CloudChannelClient.java
app/src/main/java/com/feifei/phonebridge/CloudTaskParametersContract.java
app/src/main/java/com/feifei/phonebridge/BoundedXianyuActions.java
app/src/main/java/com/feifei/phonebridge/UniqueNodeSearch.java
app/src/main/java/com/feifei/phonebridge/ImageImportContract.java
app/src/main/java/com/feifei/phonebridge/ImageImporter.java
scripts/BoundedXianyuActionsSelfTest.java
scripts/test-cloud-task-parameters.ps1
```

- `set_node_text`: `{action,package,selector:{kind,value},value}`. The body is 1–5000 Java UTF-16 characters, preserved verbatim. Selector kinds are `resource_id`, `text`, `description` with literal equality (no trim/NFKC/fuzzy matching), maximum 500 characters. Search traverses the current tree, including hidden parents, with at most 1000 node/child acquisitions and 1200 ms (also bounded by the remaining task budget). A truncated search, no match or multiple visible exact matches is rejected. The one node must remain visible, enabled, editable, non-password and in the same current Xianyu package/window immediately before `ACTION_SET_TEXT`. Only exact text from refresh of that same node produces `success=true, verified=true`; dispatched-but-unverified text is failure. Results/audit include no body, selector value or node text/description.
- `click_unique_node`: `{action,package,selector:{kind,value}}`. The same complete bounded search and current package/window checks apply. Only the matched visible/enabled/non-password clickable node receives `ACTION_CLICK`; no ancestor substitution. `success` means dispatch acceptance, with `verified=false`. It is not page/business/publishing evidence.
- `import_image`: `{action,package,mime_type,filename,sha256,data_base64}`. JPEG/PNG only; filename `oin-<lowercase sha256 first16>.jpg|png`; SHA256 is 64 hex characters. Canonical standard padded Base64 only. Streaming decode/readback rejects empty or more than 262144 raw bytes, and verifies SHA/signature/terminator. Android then checks real bitmap MIME/bounds and fully decodes it, capped at 8192 per dimension and 4194304 pixels to bound decompression. Imports into MediaStore `Pictures/OIN/`; Android 29+ uses pending rows and verified publication. Android 28 explicitly checks both declared runtime READ/WRITE_EXTERNAL_STORAGE grants and returns `image_storage_permission_required` when missing (owner grants storage permission through Android settings; no automatic permission bypass). The deterministic filename/path lookup includes pending rows, requires uniqueness and re-verifies complete stored SHA/MIME/location before reuse; conflicting or partial existing rows fail closed, never overwrite or duplicate. Successful payload only adds `filename`, `sha256`, `media_uri`, `verified` to the standard `success/message` envelope, never image/base64/body.

Bitmap/MediaStore behavior and real Accessibility execution remain unverified until APK/platform and physical-device testing. Import/dispatch does not prove the product was published. These actions add no account switching, purchase or simulated engagement.

The cloud channel never calls the local HTTP listener and has no remote Shell, arbitrary URL, arbitrary code, or unrestricted accessibility command.

For `xianyu_snapshot`, Accessibility is the only required content-access capability. Foreground-service notifications, unrestricted battery mode, auto-start, and recent-task background locking are shown as stability recommendations rather than login or recognition gates; unverifiable OEM settings remain explicitly `unknown`. Notification reading, microphone/default-assistant support, and the former local voice/WeChat command stack have been removed. OCR is a relevant future platform capability, but the current candidate does not implement screenshot capture or OCR and reports it as unavailable instead of exposing a fake switch.

## Cloud identity and transport

- The device generates a non-exportable P-256 key under the Android Keystore alias `oin_v01_device_p256` and uploads only the SubjectPublicKeyInfo public key.
- Username/password login produces a short mobile session used for enrollment or for submitting/polling an exact replacement public key. Neither password nor session token is persisted or logged. The phone cannot approve credential repair or outbox reconciliation and has no self-revoke UI/API caller; those authorizations belong to the authenticated web controller.
- The server-assigned `device_id`, account display name, and connection state are stored in app-private preferences and shown in the UI.
- The foreground `ControlService` owns the outbound WSS client, network callback, exponential reconnect, and boot recovery path. From code26, authenticated transport, the SQLite task channel/outbox, and the heavier device-state snapshot run as independent lanes. `TaskResultStore` construction is storage-I/O free and neither service startup nor reconnect prepares SQLite; only the authenticated task-channel worker opens/migrates/recovers the outbox. Code26 authentication uses an exact signed `transport_auth` object containing only its contract, compile-time app generation, and task-store protocol. It never loads a device-state cache or calls `DeviceSnapshot`; the exact minimal heartbeat starts before either secondary worker, whose bounded failure/retry cannot retire a healthy transport.
- The connection lifecycle is explicit: `BOUND_OFFLINE -> RECONNECT_WAIT -> CONNECTING -> ONLINE_FRESH -> SUSPECT`. A 55-second gap in server-to-client authenticated activity/ACK enters `SUSPECT`; only 80 seconds in that direction retires the socket. The server's 90-second timeout independently measures client-to-server inbound silence, so `80 < 90` is not described as a shared-direction margin. Every retirement registers its bounded replacement before close/cancel.
- From code27, every authenticated socket records the exact server-assigned `server_session_id` alongside the local socket epoch. A foreground-Service main-looper watchdog independently runs the same fenced lifecycle reconciliation and emits a minimal heartbeat rescue when the channel heartbeat executor has made no attempt for 35 seconds. This path performs no SQLite, device-snapshot, package, Accessibility, or outbox work; it cannot convert account binding into online evidence.
- Bounded diagnostics retain process/service instance, heartbeat send/ACK, supervisor lag, current reconnect-cycle attempt/deadline, allowlisted retire reason, socket epoch, and strict transport/device-state/outbox failure-stage enums. They contain no token, password, key, arbitrary exception text, or task/chat payload; the exact normalized diagnostics are signed with the device payload and are read-only on the owner-scoped cloud device page as well as the local status surface.
- Manual reconnect records a visible result for every preflight exit (`device_revoked`, `device_unbound`, outbox/config/network unavailable, service start/initialization/scheduler failure) instead of silently returning.
- Task protocol version `1` accepts only `health`, `observe`, `xianyu_snapshot` and the constrained `action` allowlist; parameters and validity times are validated by both server and device.
- Before execution, the task claim is committed to an app-private, transactional SQLite outbox scoped by `device_id + task_id`. The exact result is persisted before WSS send, replayed after reconnect and heartbeat ACK, and moves to normal completed state only after `task_result_ack`. A revoked foreign scope is retained until the owner authorizes an exact old/new reconciliation; late results are archived only after exact cloud persistence ACK, while unrecoverable RUNNING rows keep an `admin_revoked_interrupted` archive disposition and digest metadata. Multiple historical scopes complete one authorization at a time, while the new channel remains recovery-pending until all foreign nonterminal rows are resolved.

## Local diagnostic bridge build gate

- Debug builds retain the V2.1 diagnostic bridge on `0.0.0.0:8765` for controlled replay and compatibility work; this is plain HTTP protected by the existing random token and must be labelled diagnostic-only.
- Release builds do not start the diagnostic bridge by default. If explicitly enabled with `-PoinEnableReleaseDiagnosticBridge=true`, the default bind is loopback `127.0.0.1`.
- A release LAN bind also requires both `-PoinReleaseDiagnosticBindAddress=0.0.0.0` and `-PoinAcknowledgeLanDiagnosticExposure=true`; otherwise Gradle rejects the build.
- Cloud tasks call the Java executor directly, so production operation does not depend on the HTTP bridge.

## Reproducible debug build

Every build must inject the current approved HTTPS base URL through `-PoinCloudBaseUrl` or `OIN_CLOUD_BASE_URL`. There is no checked-in default; missing URLs, `.invalid` hosts, credentials, query and fragment are rejected:

```powershell
$env:OIN_CLOUD_BASE_URL = 'https://<approved-host>/<approved-prefix>'
$env:JAVA_HOME = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot'
$env:ANDROID_HOME = 'C:\Users\developer\Android\Sdk'
& 'C:\gradle-8.11.1\bin\gradle.bat' --no-daemon clean assembleDebug
```

All build variants now require direct HTTPS/WSS. Redirect-based HTTP upgrade and the former
debug-only cleartext exception have been removed, so the exact candidate build is:

Use the main controller's current approved base instead of copying a historical hostname. SDK35/Gradle8.11.1 setup and the final APK build are owned by the main controller, not parallel implementers.

Output: `app/build/outputs/apk/debug/app-debug.apk`.

## Local rule replay

```powershell
.\scripts\test-xianyu-replay.ps1
```

This is compatible-reader/offline rule validation. It is not an installed-APK, real-device, or no-USB control acceptance result.

Focused cloud/bounded contracts: `scripts/test-cloud-task-parameters.ps1` runs both the legacy contract and `BoundedXianyuActionsSelfTest`. On macOS/Linux with Java17:

```sh
mkdir -p app/build/bounded-action-test
javac -encoding UTF-8 -d app/build/bounded-action-test app/src/main/java/com/feifei/phonebridge/{CloudTaskParametersContract,XianyuNodeSelector,VisibleNodeSearchPolicy,BoundedXianyuActions,ImageImportContract,UniqueNodeSearch}.java scripts/{CloudTaskParametersContractSelfTest,BoundedXianyuActionsSelfTest}.java
java -classpath app/build/bounded-action-test com.feifei.phonebridge.CloudTaskParametersContractSelfTest
java -classpath app/build/bounded-action-test com.feifei.phonebridge.BoundedXianyuActionsSelfTest
```

## 当前每日任务界面：code49 / 0.16.2-v01（2026-10-14）

已绑定用户从TaskEntryActivity进入原生任务控制；设置中显示版本/检查更新。TaskRecordDialog按阶段展示动作、技术校验与回执；TaskResumeActivity先在前台恢复再打开闲鱼。通知使用ControlService原有8765前台通知，暂停/结束不触发重新连接副作用。Android10/15模拟器启动、分类切换和退出已验证；当前绑定手机通知控制未确认通过。普通预热仅首页浏览，不代表商品详情/购买已完成。最新用户要求详情上下浏览和每天最多1单、含运费≤0.01元购买，目前先重研，见任务目录buyer-path-research-20261014.md；未新增手机验证或发布50版。现金签到和游戏入口取消。

候选50 / 0.17.0-v01（未发布）：NativeTaskActivity开始/继续后直接打开闲鱼并finish，重新返回时先暂停活跃任务；主面只显示结果与恢复、重试/结束，详细步骤收在故障记录。重试先确认旧任务结束并保留未确认提交幂等号。当前公開版本49；未做50绑定手机验证，完整详情与订单流程未接通。

候选50商品数据：主分类收为预热/维护，两行短说明；ProductMetricsActivity用本设备签名metrics命令读取真实账本，显示商品名字、曝光与同口径下降建议。无采集/无在售/未确认分别显示，不填假零值。50未发布、卖家实页指标与擦亮未验证。

51 / 0.17.1-v01已发布（2026-10-14）：继承50简化任务入口/结果恢复与商品数据页。任务按钮上下padding8dp，默认上下margin6dp、左右4dp，最小触达44dp；分类按钮间距8dp，底部主按钮与提示间隔8dp。商品数据页尺寸从裸px改dp，返回按钮有明确内边距与12dp右间隔。51构建/lint和API35覆盖安装/入口与数据页打开返回通过；绑定手机流程仍待验收。

### 52 / 0.17.2-v01：全量商品维护（2026-10-14）

正常日常维护改用 `daily_maintenance_v4`：进入我发布的→按在售数量向下翻页采集全部商品→回我的→执行普通一键擦亮或略过已擦亮/无商品→返回首页正常浏览10分钟。旧v3任务快照保留，继续不会突然增加擦亮动作。

`scan_goods` 保存跨页去重、商品数、页指纹及来源observe任务编号；读满在售数量才完成，重复页/数量变化/同名商品无法区分时暂停，不能把第一页算全量。`brighten_all` 是显式的免费批量变更：仅接受完整卖家页面中唯一的“一键擦亮”或带曝光+数字的同节点，不匹配超强擦亮。已派发结果未知则冻结，不重放；已确认今日完成则跳过。付费/支付页只返回。可确认的广告关闭后重新读取。

新动作直接复用现有精确唯一节点点击和滚动通道；修正scroll_page方向语义，down向后浏览时手势向上。52客户端允许v4重试，最低API28。服务端沿用seller-native / builtin-jobs / jobs，无数据库迁移；自定义metrics含brighten_all仍要求authorize:true。版本构建与发布状态见本轮code52-release.md，测试夹具不能作为绑定手机完整流程验收。

### 53 / 0.17.3-v01：应用名称统一（2026-10-14）

应用显示名称、启动页标题和无障碍服务标签统一为OIN；启动通知改为“OIN正在启动”，不再显示OIN Android Agent/OIN工作端/PhoneBridge界面控制。保留既有包名与签名，覆盖升级不换应用身份。版本53继承52全量维护逻辑。项目转团队及FF协作是中枢项目权限变化，当前转换接口forbidden，尚未完成；不把应用改名当成团队权限已变更。


### 2026-10-15 · ADB 真机排查：任务控制与慢页面

56版修复底部“先结束当前任务”被禁用的问题：同一底部按钮在有任务时真正发出stop，不再要求寻找另一个结束入口。开始/继续检查已启用且实际连接的无障碍服务；继续先切到闲鱼再恢复任务。页面读取预算由800ms增加到3000ms，节点选择预算2400ms；依旧拒绝使用截断节点进行不确定点击。服务端慢页最多延迟重读3次，并修复恢复时旧校验时间令剩余时长膨胀的问题。57候选进一步区分“未开启”与“开关已开但服务未连接”，旧服务销毁只清理自身连接状态。

OPPO PDVM00 / Android11实测：启用列表不代表已连接。系统出现“OIN 无法运行”时dumpsys已启用但Bound为空、Crashed包含OIN；只有重新绑定后才能操作。另见ColorHansManager冻结/解冻证据、服务进程仍存在但心跳不再推进。厂商“允许完全后台行为”原已开启；本轮为OIN单独加标准电池优化豁免作隔离试验，不关闭全机省电策略。不应将此设置未经验证推广到全部Android机型。当前完整时长与发布验收见工作1000241最新回执。


### 2026-10-15 · 业务验收纠正

上一轮五段首页滑动只验证旧执行器稳定性，不证明完整预热。预热应包含实际商品详情内容阅读；日常维护必须包含卖家维护与同一套买家内容浏览。现行daily_preheat_v3/daily_maintenance_v4买家侧仍不完整，57复测已暂停。方案与待实施验收见 docs/闲鱼预热与日常维护-流程重审-20261015.md。未发布新APK。


## 0.18.3-v01（61）预热收敛候选

手机仅显示预热、本次结果、开始/继续/重新开始/结束和必要设置。移除用户侧任务记录、商品数据、维护入口、描述输入法服务注册和Root展示，历史数据不删除。旧任务保留原计划，只能结束后重新开始新版。当前流程：进商品读内容→返回列表看其他商品→收藏两件（人数+1核对，未知不重放）→搜索低价商品→购买确认页核对总价≤0.10元→交给本人确认。游戏、现金任务、留言、自动下单/付款不执行。查不到合适商品明确提示未完成。

更新为检查/可下载/下载中/已下载可安装状态；已校验下载的元数据存本地，重新进入时重新校验包名、签名、版本、大小与SHA256再安装。下载有进度、有限等待和可重试状态；不卸载、不改绑定。检查/安装成功与实际业务通过分别报告。真实手机验收见当日OIN工作报告，不能仅凭该源码说明认定已完成。

更新实页回归补充：旧更新器复用 pending.apk 的 FileProvider 地址时，OPPO安装器显示已安装的旧58版，尽管本地新包元数据为59。现改为版本号+包SHA256独立文件名/URI；用ADB覆盖60修复包绕过旧器，再通过应用自身60→61核对：安装器正确显示0.18.3，系统实际包版本61，绑定保留；该修复更新链通过。此前57→58成功不代表连续更新已验收。通知栏恢复先进入闲鱼，再经设备凭据恢复服务器任务，并拒绝旧计划。


### 0.18.4-v01（62）连接恢复补充

无障碍开关已开而服务暂断时，开始/继续自动等待系统恢复最多12秒；执行器等待受原任务剩余预算约束，并保留4秒执行余量。恢复后重新检查环境，未执行的操作只执行一次；结束与暂停不依赖无障碍恢复。页面离开或结束会撤销待继续动作。持续断连提示检查连接，不误报为权限未开。普通应用不能强制系统重绑已崩溃无障碍服务。首次任务状态加载失败时最多自动重试3次，认证拒绝不重试。
62构建/lint通过，隔离适配器检查覆盖连接已就绪、权限关闭、暂停、预算超时、请求节流、延迟绑定与中断；真机覆盖安装后Bound正常。真实瞬断自动恢复与完整预热末尾购买交接仍待真机验收。


### 0.19.0-v01（63）双功能首页

首页为“开始预热”“开始日常维护”，附简短设备连接和本次结果；无二级预热入口、打开闲鱼按钮、历史/商品数据页或大标题Logo。设置独立收起。当前任务暂停后提供继续/结束，任务未结束时不能重复开新任务。普通擦亮后，维护复用与预热相同的商品内容浏览、收藏及预算内人工购买交接；执行路径由服务端daily_maintenance_v5提供。Android11已安装并查看实际双功能页，完整流程结果另见阶段报告。


### 0.19.1-v01（64）普通擦亮实页接通

个人页“今日擦亮 曝光+5”是导航入口；真正普通按钮位于我发布的“今日数据”合并无障碍节点。tap_free_brighten仅接受action/package，手机重新读取唯一完整免费面板及在卖/草稿/已下架标记，再在已测面板内部确定免费按钮位置；不接受外部坐标，付费面板不生成点击。普通按钮变为“试试超强擦亮”后确认完成/无需重复，不继续付费。64版以上才开放新版维护。真实普通按钮已由ADB点击观察到变化，专用动作本日因已擦亮尚无再次成功点击证据。未知结果的任务可以明确结束，保留原未知操作记录。


### 0.19.2-v01（65）首页停止定时刷新

删除首页每5秒load/禁用按钮的轮询。页面进入与控制完成后读取一次；任务状态实际变化由现有后台任务监控发送进程内通知，不新增扫描或轮询。连接标签监听现有连接状态事件，未变不重绘。仍有有界失败重试；任务执行中的读取与连接心跳不受这次UI修正影响。

## 0.19.3-v01（66）可选 OIN 输入法
设置中新增“使用 OIN 输入法（可选）”，默认关闭；需要用户在系统设置启用并选择，不自动修改默认输入法。关闭时继续原有无障碍set_node_text路径。开启时，仅既有闲鱼搜索框的唯一EditText命令改走InputConnection，需确认闲鱼前台、同一窗口、输入焦点、可见搜索按钮、非密码文本字段、屏幕解锁及无障碍连接。支持替换旧搜索词和相同文字回读去重，结束/暂停/焦点变化/超时停止；写入不确定不自动回退重输。未选择或未就绪时报错，不能假称输入成功。输入法不记录输入内容、不接外部广播，不承诺降低平台风控。商品描述自动填写不恢复为产品功能。
新增SearchInputPolicySelfTest及模拟器专用ime_settings/ime_search_fixture场景；fixture是本地测试页面，不能冒充真实闲鱼验收。手机真机当前未连接，真实系统启用/选择及闲鱼搜索输入待用户更新后验证。App Manifest变化使cloudDurabilityCompatId改变，本轮不宣称完成后台长时保活验收。
