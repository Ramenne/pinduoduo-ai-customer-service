package com.feifei.phonebridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ControlActionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (ControlService.ACTION_PAUSE.equals(action)) {
            BridgeState.setPaused(context, true);
            AuditLog.append(context, "notification_pause", true, null);
            ControlService.refreshNotification(context);
        } else if (ControlService.ACTION_RESUME.equals(action)) {
            BridgeState.setPaused(context, false);
            AuditLog.append(context, "notification_resume", true, null);
            ControlService.refreshNotification(context);
        } else if (ControlService.ACTION_STOP.equals(action)) {
            context.stopService(new Intent(context, ControlService.class));
        }
    }
}
