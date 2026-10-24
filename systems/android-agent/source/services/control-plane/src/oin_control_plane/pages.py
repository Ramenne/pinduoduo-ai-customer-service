from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from .config import Settings


CSS = """
:root { color-scheme: light; font-family: Inter, system-ui, -apple-system, Segoe UI, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f9fc; color: #142033; }
header { background: #fff; border-bottom: 1px solid #dfe8f3; }
nav, main { width: min(1040px, calc(100% - 32px)); margin: 0 auto; }
nav { min-height: 64px; display: flex; align-items: center; gap: 18px; }
nav strong { margin-right: auto; }
a { color: #175cd3; text-decoration: none; }
main { padding: 34px 0 64px; }
.card { background: #fff; border: 1px solid #dfe8f3; border-radius: 14px; padding: 22px; margin: 0 0 18px; box-shadow: 0 8px 30px rgba(36, 78, 120, .06); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }
label { display: block; font-weight: 650; margin: 14px 0 6px; }
input, select { width: 100%; padding: 11px 12px; border: 1px solid #b9c8da; border-radius: 9px; font: inherit; background: #fff; }
button, .button { display: inline-block; border: 0; border-radius: 9px; padding: 11px 16px; margin: 12px 8px 0 0; background: #175cd3; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
button.danger { background: #b42318; }
.muted { color: #5f6f83; }
.error { color: #b42318; background: #fff1f0; border: 1px solid #ffd2cc; padding: 10px; border-radius: 8px; }
.ok { color: #067647; }
.warning { color: #b54708; }
.badge { display: inline-block; padding: 4px 9px; border-radius: 999px; background: #eef4ff; color: #1849a9; font-size: 13px; }
dl { display: grid; grid-template-columns: minmax(130px, 210px) 1fr; gap: 10px 16px; }
dt { color: #5f6f83; }
dd { margin: 0; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 460px; overflow: auto; padding: 14px; border-radius: 10px; background: #f7f9fc; border: 1px solid #e3eaf3; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; vertical-align: top; padding: 10px 8px; border-bottom: 1px solid #e7edf5; }
.qr { width: min(280px, 100%); background: #fff; border: 12px solid #fff; }
@media (max-width: 640px) { dl { grid-template-columns: 1fr; gap: 4px; } dd { margin-bottom: 10px; } table { font-size: 14px; } }
"""

STATUS_REASON_TEXT = {
    "device_offline": "设备离线：请在手机端检查网络并点击立即重连。",
    "realtime_socket_absent": "数据库会话仍新鲜，但当前控制平面进程没有匹配的实时 socket；不视为可执行在线。",
    "task_channel_not_ready": "传输心跳在线，但持久任务存储尚未恢复就绪；新任务保持 queued，服务器不会派发。",
    "device_state_stale": "传输仍可在线，但设备型号、权限、版本或诊断快照已陈旧；等待独立设备状态刷新。",
    "accessibility_not_enabled": "上次手机快照报告无障碍未授权；若已开启，须先恢复 WS/心跳才能更新。",
    "accessibility_not_connected": "上次手机快照报告无障碍已授权但服务未连接；返回手机端重新检测。",
    "battery_optimization_not_ignored": "上次手机快照未确认电池优化豁免；后台连接可能被 OEM 回收。",
    "xianyu_not_installed": "未检测到闲鱼应用；xianyu_snapshot 不可用。",
    "xianyu_live_compatibility_unverified": "闲鱼只读规则存在，但当前安装版本尚未完成真机兼容验收。",
}

TASK_ERROR_GUIDANCE = {
    "accessibility_not_connected": "在手机端开启无障碍并确认状态为已连接。",
    "accessibility_unavailable": "无障碍未授权或服务未连接；闲鱼读取与操作已安全停止。",
    "screen_off": "手机屏幕已关闭；云通道仍可在线，但闲鱼任务不会盲目执行。",
    "device_locked": "手机处于锁屏状态；请人工解锁并把闲鱼置于前台后重试。",
    "system_ui_foreground": "系统界面、通知栏或锁屏层位于前台；未假定其具体来源，闲鱼任务已停止。",
    "xianyu_not_foreground": "当前前台不是闲鱼；请人工把闲鱼置于前台后重试。",
    "execution_environment_unknown": "系统无法可靠确认屏幕、锁屏或当前根窗口；任务已按未知状态停止。",
    "task_expired": "任务已过期；确认设备在线后创建新任务。",
    "task_expired_before_execution": "任务排队期间过期；缩短设备本地任务队列后重试。",
    "device_revoked": "设备凭证已撤销；在手机端重新绑定新身份。",
    "result_too_large_for_outbox": "结果超过手机 outbox 上限；缩小采集范围后重试。",
    "task_interrupted_before_durable_result": "任务执行中进程被回收；结果未伪造，可重新创建任务。",
    "local_executor_error": "本地执行器异常；在手机端查看错误码并重新检测权限。",
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _time(value: int | None) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _time_ms(value: int | None) -> str:
    if not value:
        return "—"
    return _time(value // 1000)


def _truth(value: Any, yes: str, no: str) -> str:
    return yes if value is True else no


def layout(
    settings: Settings,
    title: str,
    body: str,
    *,
    username: str | None = None,
    csrf: str | None = None,
) -> str:
    links = [f'<a href="{_e(settings.browser_path("/download"))}">下载 APK</a>']
    if username:
        links.insert(0, f'<a href="{_e(settings.browser_path("/devices"))}">设备</a>')
        links.append(
            f'<form method="post" action="{_e(settings.browser_path("/logout"))}" style="display:inline">'
            f'<input type="hidden" name="csrf" value="{_e(csrf)}"><button style="margin:0">退出</button></form>'
        )
    else:
        links.insert(0, f'<a href="{_e(settings.browser_path("/login"))}">登录</a>')
        links.insert(1, f'<a href="{_e(settings.browser_path("/register"))}">注册</a>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · OIN</title><link rel="icon" href="{_e(settings.browser_path('/oin-brand.svg'))}" type="image/svg+xml"><style>{CSS}</style></head>
<body><header><nav><strong style="display:flex;align-items:center;gap:10px"><img src="{_e(settings.browser_path('/oin-brand.svg'))}" alt="" width="32" height="32">OIN 工作台</strong>{''.join(links)}</nav></header>
<main>{body}</main></body></html>"""


def auth_form(
    settings: Settings,
    *,
    mode: str,
    csrf: str,
    error: str | None = None,
    username: str = "",
    email: str = "",
) -> str:
    register = mode == "register"
    heading = "创建 OIN 应用账号" if register else "登录 OIN"
    action = settings.browser_path("/register" if register else "/login")
    fields = ""
    if register:
        fields += f'<label>用户名</label><input name="username" value="{_e(username)}" autocomplete="username" required minlength="3" maxlength="48">'
        fields += f'<label>邮箱（可选）</label><input name="email" value="{_e(email)}" autocomplete="email" type="email" maxlength="254">'
    else:
        fields += f'<label>用户名或邮箱</label><input name="identifier" value="{_e(username)}" autocomplete="username" required maxlength="254">'
    password_autocomplete = "new-password" if register else "current-password"
    fields += f'<label>密码</label><input name="password" type="password" autocomplete="{password_autocomplete}" required minlength="12" maxlength="128">'
    error_html = f'<p class="error">{_e(error)}</p>' if error else ""
    body = f"""<section class="card" style="max-width:520px;margin:auto"><h1>{heading}</h1>
    <p class="muted">这里的账号是 OIN 应用账号，与腾讯云、Linux 或数据库账号完全分离。</p>
    {error_html}<form method="post" action="{_e(action)}">
    <input type="hidden" name="csrf" value="{_e(csrf)}">{fields}
    <button type="submit">{'注册账号' if register else '登录'}</button></form>
    <p><a href="{_e(settings.browser_path('/login'))}">已有账号，登录</a>
    · <a href="{_e(settings.browser_path('/register'))}">没有账号，注册</a></p></section>"""
    return layout(settings, heading, body)


def devices_page(settings: Settings, username: str, csrf: str, devices: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for device in devices:
        state_class = "ok" if device["state"] == "ONLINE_READY" else "warning"
        reasons = device.get("status_reasons", [])
        reason_html = "".join(
            f'<li>{_e(STATUS_REASON_TEXT.get(reason, reason))}</li>' for reason in reasons[:3]
        )
        cards.append(
            f"""<article class="card"><span class="badge {state_class}">{_e(device['state'])}</span>
            <h2><a href="{_e(settings.browser_path('/devices/' + device['id']))}">{_e(device['display_name'])}</a></h2>
            <p><a class="button" href="{_e(settings.browser_path('/devices/' + device['id'] + '/seller'))}">闲鱼上新与维护</a></p>
            <p>{_e(device['manufacturer'])} {_e(device['model'])} · Android {_e(device['android_version'])} · App {_e(device['app_version'])}</p>
            <p class="muted">传输：{'online' if device['online'] else 'offline'} · 任务通道：{'ready' if device['task_channel_ready'] else device['task_store_state'] or 'not-ready'} · 设备状态：{_e(device['device_state'])}</p>
            <p class="muted">最后传输心跳：{_e(_time(device['last_seen_at']))}</p>
            <p class="muted">待处理任务：{device['pending_task_count']} · 最老排队 {device['oldest_pending_task_age_seconds'] if device['oldest_pending_task_age_seconds'] is not None else '—'} 秒 · 最近重连空窗 {device['last_reconnect_gap_seconds'] if device['last_reconnect_gap_seconds'] is not None else '—'} 秒</p>
            {f'<ul class="warning">{reason_html}</ul>' if reason_html else '<p class="ok">当前未发现已知阻断。</p>'}</article>"""
        )
    if not cards:
        cards.append(
            '<section class="card"><h2>尚无已绑定设备</h2><p class="muted">在 Android Agent 中登录后，设备会用 Keystore 公钥完成注册并显示在这里。</p></section>'
        )
    body = f"<h1>{_e(username)} 的设备</h1>{''.join(cards)}"
    return layout(settings, "设备", body, username=username, csrf=csrf)


def device_page(
    settings: Settings,
    username: str,
    csrf: str,
    device: dict[str, Any],
    tasks: list[dict[str, Any]],
    owner_devices: list[dict[str, Any]],
    credential_repair_requests: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
) -> str:
    capabilities = device["capabilities"]
    is_xiaomi = "xiaomi" in str(device.get("manufacturer", "")).lower()
    task_rows: list[str] = []
    for task in tasks:
        reported = task.get("reported_result")
        if reported is not None and task.get("result_disposition") != "on_time":
            rendered = (
                f"处置：{task.get('result_disposition') or 'unknown'}\n"
                f"云端接收：{_time(task.get('result_received_at'))}\n" +
                json.dumps(reported, ensure_ascii=False, indent=2)
            )
        elif task["error"] is not None:
            code = task["error"].get("code") or "unknown_task_error"
            guidance = TASK_ERROR_GUIDANCE.get(code, "保留此错误码并在手机端查看对应诊断入口。")
            rendered = (
                f"错误码：{code}\n"
                f"说明：{task['error'].get('message') or '—'}\n"
                f"修复：{guidance}"
            )
        elif task["result"] is not None:
            rendered = json.dumps(task["result"], ensure_ascii=False, indent=2)
        else:
            rendered = "—"
        task_rows.append(
            f"<tr><td>{_e(task['task_type'])}</td><td>{_e(task.get('effective_status') or task['status'])}</td>"
            f"<td>{_e(_time(task['issued_at']))}</td><td><pre>{_e(rendered[:100_000])}</pre></td></tr>"
        )
    if not task_rows:
        task_rows.append('<tr><td colspan="4" class="muted">尚无任务</td></tr>')

    task_buttons = ""
    if device["binding_state"] == "bound":
        available_tasks: list[tuple[str, str]] = []
        advertised_tasks = set(capabilities.get("cloud_task_types") or [])
        if device["online"] and device["task_channel_ready"]:
            if "health" in advertised_tasks:
                available_tasks.append(("health", "运行 health"))
            if capabilities.get("accessibility_connected"):
                if "observe" in advertised_tasks:
                    available_tasks.append(("observe", "运行 observe"))
                if "xianyu_snapshot" in advertised_tasks:
                    available_tasks.append(("xianyu_snapshot", "运行闲鱼 snapshot"))
        for task_type, label in available_tasks:
            task_buttons += (
                f'<form method="post" action="{_e(settings.browser_path("/devices/" + device["id"] + "/tasks"))}" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}"><input type="hidden" name="task_type" value="{task_type}">'
                f'<button type="submit">{label}</button></form>'
            )
        if (
            device["online"]
            and device["task_channel_ready"]
            and "action" in advertised_tasks
        ):
            task_buttons += (
                f'<form method="post" action="{_e(settings.browser_path("/devices/" + device["id"] + "/tasks"))}" style="display:inline">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                '<input type="hidden" name="task_type" value="action">'
                '<input type="hidden" name="action" value="launch_app">'
                '<input type="hidden" name="package" value="com.taobao.idlefish">'
                '<button type="submit">启动闲鱼（受限动作）</button></form>'
            )
        if not device["online"]:
            task_buttons = (
                '<p class="warning">设备当前离线；网页不创建必然等待并可能过期的交互任务。'
                'API 仍保留带 TTL 的离线排队能力。</p>'
            )
        elif not device["task_channel_ready"]:
            task_buttons = (
                '<p class="warning">设备仅诊断在线；手机端持久任务存储尚未恢复就绪。'
                '网页与 API 均拒绝创建任务，服务器也不会向该连接派发历史任务。</p>'
            )
        elif not capabilities.get("accessibility_connected"):
            task_buttons += (
                '<p class="warning">observe / xianyu_snapshot 需要上次快照确认无障碍已连接；'
                '当前仅开放 health。</p>'
            )
    details = [
        ("device_id", device["id"]),
        ("状态", device["state"]),
        ("设备", f"{device['manufacturer']} {device['brand']} {device['model']}"),
        ("Android", f"{device['android_version']} / SDK {device['sdk']}"),
        ("Agent", f"{device['app_version']} ({device['app_version_code']})"),
        ("执行内核", device["kernel_version"]),
        (
            "Realtime transport",
            f"{'online' if device['online'] else 'offline'} · session {device.get('current_session_id') or 'none'} · last heartbeat {_time(device['last_seen_at'])}",
        ),
        (
            "实时观测",
            f"activity age {device['transport_activity_age_seconds'] if device['transport_activity_age_seconds'] is not None else '—'}s · offline {device['offline_duration_seconds'] if device['offline_duration_seconds'] is not None else '—'}s · last reconnect gap {device['last_reconnect_gap_seconds'] if device['last_reconnect_gap_seconds'] is not None else '—'}s",
        ),
        (
            "待处理任务",
            f"count {device['pending_task_count']} · oldest age {device['oldest_pending_task_age_seconds'] if device['oldest_pending_task_age_seconds'] is not None else '—'}s · nearest TTL {device['nearest_pending_task_ttl_seconds'] if device['nearest_pending_task_ttl_seconds'] is not None else '—'}s",
        ),
        (
            "任务通道",
            (
                "恢复协议 v2 · 已就绪"
                if device["task_store_protocol"] == 2 and device["task_channel_ready"]
                else (
                    f"恢复协议 v2 · 未就绪（{device['task_store_state'] or 'unknown'}）"
                    if device["task_store_protocol"] == 2
                    else "旧协议兼容 · 未声明持久恢复状态"
                )
            ),
        ),
        (
            "任务通道阶段",
            f"{device['task_channel_failure_stage']} · {_time(device['task_channel_updated_at'])}",
        ),
        (
            "设备状态",
            f"{device['device_state']} · {device['device_state_failure_stage']} · {_time(device['device_state_updated_at'])}",
        ),
        ("时区 / 网络", f"{device['timezone']} / {device['network_type']}"),
    ]
    diagnostics = device.get("diagnostics") or {}
    if diagnostics:
        details.extend(
            [
                ("进程实例", f"{diagnostics['process_instance_id']} / PID {diagnostics['process_pid']} / {_time_ms(diagnostics['process_started_at_ms'])}"),
                ("服务实例", f"{diagnostics['service_instance_id']} / {_time_ms(diagnostics['service_started_at_ms'])}"),
                ("手机 lifecycle", diagnostics["lifecycle_state"]),
                ("手机 heartbeat send / ACK", f"{_time_ms(diagnostics['last_heartbeat_sent_at_ms'])} / {_time_ms(diagnostics['last_heartbeat_ack_at_ms'])}"),
                ("supervisor lag", f"{diagnostics['supervisor_lag_ms']} ms"),
                ("socket epoch / 当前重连周期 attempt", f"{diagnostics['socket_epoch']} / {diagnostics['reconnect_attempt']}"),
                ("重连期限", _time_ms(diagnostics["reconnect_deadline_at_ms"])),
                ("上次退役原因", diagnostics["last_retire_reason"]),
            ]
        )
        if "transport_failure_stage" in diagnostics:
            details.extend(
                [
                    ("transport failure stage", diagnostics["transport_failure_stage"]),
                    ("device-state failure stage", diagnostics["device_state_failure_stage"]),
                    ("outbox failure stage", diagnostics["outbox_failure_stage"]),
                ]
            )
        if "server_session_id" in diagnostics:
            details.extend(
                [
                    ("手机回报 server session", diagnostics["server_session_id"]),
                    ("service watchdog lag", f"{diagnostics['service_watchdog_lag_ms']} ms"),
                ]
            )
        if "last_recovery_outcome" in diagnostics:
            details.extend(
                [
                    (
                        "最大调度 / service watchdog lag",
                        f"{diagnostics['max_supervisor_lag_ms']} / {diagnostics['max_service_watchdog_lag_ms']} ms",
                    ),
                    (
                        "最近恢复周期",
                        f"{_time_ms(diagnostics['last_recovery_started_at_ms'])} → {_time_ms(diagnostics['last_recovery_completed_at_ms'])}",
                    ),
                    (
                        "最近恢复结果",
                        f"{diagnostics['last_recovery_outcome']} · attempts {diagnostics['last_recovery_attempts']} · trigger {diagnostics['last_recovery_trigger']} · stage {diagnostics['last_recovery_failure_stage']}",
                    ),
                ]
            )
    detail_html = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in details)
    reasons = device.get("status_reasons", [])
    reason_html = "".join(
        f'<li><code>{_e(reason)}</code> · {_e(STATUS_REASON_TEXT.get(reason, reason))}</li>'
        for reason in reasons
    ) or '<li class="ok">当前未发现已知阻断。</li>'
    permission_details = [
        (
            "无障碍授权",
            _truth(capabilities.get("accessibility_enabled"), "已授权", "未授权"),
        ),
        (
            "无障碍连接",
            _truth(capabilities.get("accessibility_connected"), "已连接", "未连接"),
        ),
        (
            "通知读取",
            "本轮不需要，Android Agent 已移除监听能力",
        ),
        (
            "前台服务通知",
            _truth(
                capabilities.get("notification_enabled"),
                "系统允许 Agent 发布通知；不等同于通知此刻必然可见",
                "系统未允许 Agent 发布通知；前台服务可能被 OEM 限制",
            ),
        ),
        (
            "截图",
            "本轮不需要，Android Agent 已移除截图能力",
        ),
        (
            "电池无限制",
            _truth(
                capabilities.get("battery_optimization_ignored"),
                "已豁免 / 无限制",
                "未豁免；属于后台稳定性建议，不是节点读取权限",
            ),
        ),
        (
            "自启动 / 后台锁定",
            (
                {
                    "enabled": "手机端报告已开启",
                    "disabled": "手机端报告未开启",
                    "unknown": (
                        "手机端无法可靠检测；建议按 OEM 设置人工核对自启动和最近任务锁定"
                        if is_xiaomi
                        else "手机端无法可靠检测，需在 OEM 设置人工核对"
                    ),
                }.get(
                    capabilities.get("background_start_status"),
                    (
                        "旧客户端由用户确认已配置"
                        if capabilities.get("background_start_user_configured")
                        else (
                            "旧客户端未提供可验证状态；建议按 OEM 设置人工核对"
                            if is_xiaomi
                            else "旧客户端未提供可验证状态，需在 OEM 设置人工核对"
                        )
                    ),
                )
            ),
        ),
    ]
    permission_html = "".join(
        f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in permission_details
    )
    background_note = (
        "闲鱼前台节点读取的唯一必需权限是无障碍；应用通知、电池豁免、OEM 自启动和"
        "最近任务锁定均是后台存活稳定性建议。未配置可能降低持续在线率，但不能写成内容读取必要授权。"
    )
    plugin_details = [
        ("闲鱼安装", _truth(capabilities.get("xianyu_installed"), "已检测到", "未检测到")),
        ("已安装版本", capabilities.get("xianyu_version") or "未知/未安装"),
        ("内置规则版本", capabilities.get("xianyu_rules_version") or "未知"),
        (
            "规则状态",
            "真机兼容已确认" if capabilities.get("xianyu_rules_verified") else "只读规则存在，当前版本真机兼容待验",
        ),
        ("启停状态", "内置只读；无自动动作开关"),
        (
            "前台 App 边界",
            "snapshot 本身只读；板块 0 仅允许受限启动闲鱼、返回和回桌面，不允许任意应用或页面内业务点击",
        ),
        ("允许任务", ", ".join(capabilities.get("cloud_task_types") or [])),
    ]
    plugin_html = "".join(
        f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in plugin_details
    )
    reliability_details = [
        ("本地执行内核", capabilities.get("local_executor_version") or device["kernel_version"]),
        (
            "任务存储",
            capabilities.get("task_store_state")
            if device["task_store_protocol"] == 2
            else "legacy / 未声明",
        ),
        ("截图 API", "已移除；xianyu_snapshot 只读取无障碍节点树"),
        ("ROOT", "检测到文件痕迹；OIN 不使用 ROOT" if capabilities.get("root_signal") else "免 ROOT 运行；未检测到文件痕迹"),
        (
            "结果恢复",
            "持久任务存储已就绪；仍以 task ACK/result ACK 为执行证据"
            if device["task_store_protocol"] == 2 and device["task_channel_ready"]
            else (
                "持久任务存储未就绪；服务器当前禁止创建和派发任务"
                if device["task_store_protocol"] == 2
                else "旧协议只证明兼容接入，不声明恢复握手已完成"
            ),
        ),
    ]
    reliability_html = "".join(
        f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in reliability_details
    )
    revoke = ""
    if device["binding_state"] == "bound":
        revoke = f"""<form method="post" action="{_e(settings.browser_path('/devices/' + device['id'] + '/revoke'))}">
        <input type="hidden" name="csrf" value="{_e(csrf)}"><button class="danger" type="submit">撤销设备凭证</button></form>"""
    repair_rows: list[str] = []
    for repair in credential_repair_requests:
        action = ""
        if repair["state"] == "pending_controller_approval":
            action = (
                f'<form method="post" action="{_e(settings.browser_path("/devices/" + device["id"] + "/credential-repair-requests/" + repair["id"] + "/approve"))}">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                '<button type="submit">批准此精确公钥</button></form>'
            )
        repair_rows.append(
            f"<tr><td>{_e(repair['id'])}</td><td><code>{_e(repair['public_key_sha256'])}</code></td>"
            f"<td>{_e(repair['state'])}</td><td>{_e(_time(repair['created_at']))}</td><td>{action}</td></tr>"
        )
    repair_html = "".join(repair_rows) or '<tr><td colspan="5" class="muted">尚无凭证修复请求</td></tr>'

    reconciliation_rows = "".join(
        f"<tr><td>{_e(item['old_device_id'])}</td><td>{_e(item['state'])}</td>"
        f"<td>{_e(_time(item['created_at']))}</td><td>{_e(_time(item.get('completed_at')))}</td>"
        f"<td>{_e(_time(item.get('device_acknowledged_at')))}</td></tr>"
        for item in reconciliations
    ) or '<tr><td colspan="5" class="muted">尚无 old/new scope reconciliation</td></tr>'
    revoked_options = "".join(
        f'<option value="{_e(candidate["id"])}">{_e(candidate["display_name"])} · {_e(candidate["id"])}</option>'
        for candidate in owner_devices
        if candidate["binding_state"] == "revoked" and candidate["id"] != device["id"]
    )
    reconciliation_form = ""
    if device["binding_state"] == "bound" and revoked_options:
        reconciliation_form = (
            f'<form method="post" action="{_e(settings.browser_path("/devices/" + device["id"] + "/outbox-reconciliations"))}">'
            f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
            f'<label>已撤销 old scope<select name="old_device_id">{revoked_options}</select></label>'
            '<button type="submit">授权一次性 reconciliation</button></form>'
        )
    body = f"""<p><a href="{_e(settings.browser_path('/devices'))}">← 返回设备列表</a></p>
    <section class="card"><span class="badge">{_e(device['state'])}</span><h1>{_e(device['display_name'])}</h1><dl>{detail_html}</dl></section>
    <section class="card"><h2>连接与诊断</h2><ul>{reason_html}</ul></section>
    <section class="card"><h2>权限与后台稳定性</h2><dl>{permission_html}</dl><p class="muted">{_e(background_note)}状态来自 {_e(_time(device['last_seen_at']))} 的最后成功手机快照，不是网页实时读取；WS/心跳中断时，手机后续修改不会显示。</p></section>
    <section class="card"><h2>闲鱼只读插件</h2><dl>{plugin_html}</dl><p class="muted">xianyu_snapshot 仍只读取无障碍节点树；消息/聊天规则未经真机验证。探索阶段另提供闲鱼限定的启动、当前可见节点精确点击、上下滚动和返回；不开放任意包名、任意坐标、输入或远程 Shell。</p></section>
    <section class="card"><h2>运行可靠性</h2><dl>{reliability_html}</dl></section>
    <section class="card"><h2>凭证修复审批</h2><p class="muted">手机只能提交候选公钥；这里的同账号 Web 控制端审批才会签发一次性挑战。审批保留当前 device_id。</p><table><thead><tr><th>请求</th><th>公钥 SHA-256</th><th>状态</th><th>创建</th><th>操作</th></tr></thead><tbody>{repair_html}</tbody></table></section>
    <section class="card"><h2>撤销后 outbox reconciliation</h2><p class="muted">仅允许同 owner、同一安装实例、old 已 revoked、new 已 bound 的精确 scope 对。手机先上传可落库的迟到结果，再把不可恢复 RUNNING 归档为 admin_revoked_interrupted；本机不盲删。</p>{reconciliation_form}<table><thead><tr><th>old scope</th><th>状态</th><th>创建</th><th>云端条目完成</th><th>手机确认归档</th></tr></thead><tbody>{reconciliation_rows}</tbody></table></section>
    <section class="card"><h2>设备测试（云端）</h2><p class="muted">手机端不再放置本机自检。设备在线且任务通道就绪时，这里集中提供 health、observe、xianyu_snapshot 和受限 action；health 会返回锁屏、屏幕交互、无障碍及真实前台包状态。闲鱼读取、点击、滑动与后续输入只有在执行环境为 ready 时才运行；否则返回明确原因，不盲目操作。没有远程 Shell、任意包启动或开放式坐标控制。</p>{task_buttons}</section>
    <section class="card"><h2>最近任务</h2><table><thead><tr><th>类型</th><th>状态</th><th>时间</th><th>结果</th></tr></thead><tbody>{''.join(task_rows)}</tbody></table></section>
    <section class="card"><h2>解除绑定</h2><p class="muted">撤销是终态：旧凭证立即失效。之后重新登录会用新密钥创建新 device_id；旧 scope 的非终态 outbox 仍保留，需通过上方明确授权收口。</p>{revoke}</section>"""
    return layout(settings, device["display_name"], body, username=username, csrf=csrf)


def download_page(
    settings: Settings,
    *,
    apk_available: bool,
    apk_sha256: str | None,
    username: str | None = None,
    csrf: str | None = None,
) -> str:
    if apk_available:
        apk_url = settings.public_url("/download/apk")
        availability = f"""<p class="ok">版本：{_e(settings.apk_version)}</p>
        <p>SHA-256：<code>{_e(apk_sha256)}</code></p>
        <p><a class="button" href="{_e(apk_url)}">直接下载 APK</a></p>
        <h2>手机扫码下载</h2>
        <p class="muted">扫码后直接下载同一 APK，不经过中间页面。</p>
        <img class="qr" src="{_e(settings.browser_path('/download/qr.svg'))}" alt="直接下载 APK 的二维码">"""
    else:
        availability = """<p class="warning">当前服务器尚未发布 APK。下载与二维码只有在部署方显式配置 OIN_APK_PATH 并校验发布哈希后才启用。</p>"""
    body = f"""<section class="card"><h1>Android Agent 下载</h1>{availability}
    <p class="muted">首次安装仍需用户手工允许安装来源，并手工开启无障碍。前台服务通知、电池无限制、自启动与最近任务锁定是后台存活稳定性建议，不是节点读取的必要授权；各 OEM 可按系统策略核对。通知读取、悬浮窗、截图、麦克风、默认助手和 ROOT 均不是 OIN / 闲鱼只读前置条件。免 USB 不等于绕过 Android 权限。</p></section>"""
    return layout(settings, "下载 APK", body, username=username, csrf=csrf)
