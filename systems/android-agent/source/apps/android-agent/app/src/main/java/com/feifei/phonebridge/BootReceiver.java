package com.feifei.phonebridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) ||
                Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            String event = Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)
                    ? "package_replaced_received" : "boot_completed_received";
            ControlService.requestRecovery(context, event);
        }
    }
}
