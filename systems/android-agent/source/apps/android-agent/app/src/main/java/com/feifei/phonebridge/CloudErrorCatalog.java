package com.feifei.phonebridge;

final class CloudErrorCatalog {
    enum Action {
        NONE,
        RETRY,
        REPAIR_IDENTITY,
        OPEN_APP_SETTINGS
    }

    static final class Entry {
        final String code;
        final String message;
        final Action action;

        Entry(String code, String message, Action action) {
            this.code = code;
            this.message = message;
            this.action = action;
        }
    }

    private CloudErrorCatalog() {
    }

    static Entry explain(String suppliedCode) {
        String code = suppliedCode == null || suppliedCode.isEmpty()
                ? "unknown_error"
                : suppliedCode;
        switch (code) {
            case "invalid_credentials":
                return new Entry(code, "账号或密码错误；请核对 OIN 应用账号后重试。", Action.RETRY);
            case "rate_limited":
                return new Entry(code, "登录尝试过多；请稍后再试。", Action.RETRY);
            case "invalid_device_signature":
                return new Entry(code, "设备签名未通过；请发起保留 device_id 的云端审批凭证修复。", Action.REPAIR_IDENTITY);
            case "device_key_conflict":
                return new Entry(code, "此设备密钥已绑定或已撤销；请在网页核对旧设备记录。", Action.REPAIR_IDENTITY);
            case "device_private_key_missing":
            case "device_private_key_unusable":
            case "device_keystore_unavailable":
            case "device_identity_unavailable":
                return new Entry(code, "Android Keystore 设备密钥不可用；请发起云端审批凭证修复，不要清除应用数据。", Action.REPAIR_IDENTITY);
            case "device_revoked":
                return new Entry(code, "设备凭证已由网页控制端撤销；可重新登录创建新设备身份。", Action.REPAIR_IDENTITY);
            case "device_unbound":
                return new Entry(code, "本机没有 device_id；请先在网页核对旧设备，再登录绑定。", Action.RETRY);
            case "cloud_network_error":
            case "cloud_connection_failed":
            case "cloud_disconnected":
            case "network_unavailable":
                return new Entry(code, "网络或服务连接不可用；检查网络后立即重连。", Action.RETRY);
            case "cloud_base_url_not_configured":
                return new Entry(code, "当前 APK 未配置可用云端地址；请安装正确发布版本。", Action.NONE);
            case "task_result_outbox_unavailable":
                return new Entry(
                        code,
                        "任务结果存储不可用；诊断通道可继续连接，但任务执行已禁用。请保留应用数据并检查存储状态。",
                        Action.OPEN_APP_SETTINGS
                );
            case "task_store_sqlite_full":
            case "task_store_sqlite_io":
            case "task_store_sqlite_error":
            case "task_store_sqlite_corrupt":
            case "task_store_unavailable":
            case "task_store_not_ready":
                return new Entry(
                        code,
                        "本机任务存储故障；已停止接收任务但保留云端诊断连接。请勿清除应用数据或重建设备身份。",
                        Action.OPEN_APP_SETTINGS
                );
            case "legacy_scope_mismatch":
            case "legacy_current_scope_unbound":
            case "foreign_scope_nonterminal":
                return new Entry(
                        code,
                        "检测到已撤销旧设备的任务账本；已隔离保留，等待 Web 控制端授权 old/new scope reconciliation，不能在本机盲清。",
                        Action.NONE
                );
            case "binding_persistence_failed":
                return new Entry(code, "服务器已响应绑定，但本机同步持久化失败；未显示成功，可用同一账号和密钥安全重试。", Action.RETRY);
            case "credential_activation_persistence_failed":
            case "identity_activation_marker_persistence_failed":
            case "device_identity_state_persistence_failed":
                return new Entry(code, "新凭证已准备但本机激活状态未可靠落盘；请保留应用数据并重试，不要重建 scope。", Action.RETRY);
            case "revoked_device_cannot_rotate_credential":
                return new Entry(code, "旧设备已正式撤销，不能恢复其凭证；请重新登录绑定新 device_id，并由 Web 控制端收口旧 outbox。", Action.RETRY);
            case "rebind_owner_mismatch":
                return new Entry(code, "撤销后的新设备必须继续归属原 OIN 账号；跨账号重绑已拒绝。", Action.RETRY);
            case "credential_repair_owner_mismatch":
                return new Entry(code, "凭证修复账号与当前设备 owner 不一致，候选密钥未提交。", Action.REPAIR_IDENTITY);
            case "task_result_outbox_capacity":
                return new Entry(code, "待确认任务结果已满；保持联网等待云端 ACK。", Action.RETRY);
            case "heartbeat_failed":
                return new Entry(code, "心跳发送失败；连接正在重建。", Action.RETRY);
            case "heartbeat_ack_suspect":
                return new Entry(code, "超过 55 秒未收到 ACK；正在宽限期内主动探测，尚未取消连接。", Action.NONE);
            case "heartbeat_ack_grace_exhausted":
                return new Entry(code, "80 秒仍未收到服务端 ACK；已按客户端确认方向的独立硬期限登记重连并退役旧连接。", Action.RETRY);
            case "heartbeat_ack_timeout":
                return new Entry(code, "旧版本在 55 秒 ACK 空窗后直接重连。", Action.RETRY);
            case "cloud_auth_timeout":
                return new Entry(code, "云连接在 30 秒内未完成认证；旧连接已关闭并正在重建。", Action.RETRY);
            case "cloud_supervisor_unavailable":
            case "cloud_supervisor_iteration_failed":
                return new Entry(code, "云通道监督异常；前台服务将继续按有界退避恢复。", Action.RETRY);
            case "service_transport_watchdog_failed":
                return new Entry(code, "前台服务的独立云通道监督本轮异常；下一轮仍会继续核对心跳与重连。", Action.RETRY);
            case "service_running":
                return new Entry(code, "前台服务正在运行。", Action.NONE);
            case "service_start_requested":
                return new Entry(code, "已请求启动前台服务；状态将自动刷新。", Action.NONE);
            case "reconnect_request_dispatched":
                return new Entry(code, "重连请求已交给前台服务；实际早退或连接结果将自动刷新。", Action.NONE);
            case "reconnect_scheduled":
                return new Entry(code, "前置检查通过，已立即安排云端连接。", Action.NONE);
            case "service_not_running":
                return new Entry(code, "前台服务未运行，无法建立云端连接。", Action.OPEN_APP_SETTINGS);
            case "service_start_failed":
                return new Entry(code, "Android 拒绝启动前台服务；请检查应用通知、后台和电池设置。", Action.OPEN_APP_SETTINGS);
            case "service_initialization_failed":
                return new Entry(code, "前台服务启动后初始化失败；请打开应用设置后重试。", Action.OPEN_APP_SETTINGS);
            case "service_scheduler_unavailable":
                return new Entry(code, "重连调度器不可用；请重新打开应用启动前台服务。", Action.RETRY);
            case "service_stopped":
                return new Entry(code, "前台服务已停止。", Action.RETRY);
            case "never_requested":
                return new Entry(code, "尚未发起重连。", Action.NONE);
            case "cloud_protocol_rejected":
            case "cloud_protocol_error":
                return new Entry(code, "云端协议消息不兼容；核对 APK 与服务器版本。", Action.RETRY);
            default:
                return new Entry(code, "操作失败；错误码 " + code + "。", Action.RETRY);
        }
    }
}
