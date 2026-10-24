package com.feifei.phonebridge;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;

/** Runs the real private Activity without credentials, task creation or an exported test entry. */
public final class TaskCenterLaunchInstrumentation extends Instrumentation {
    private Bundle arguments;
    @Override public void onCreate(Bundle args) { super.onCreate(args); arguments=args; start(); }
    private android.view.View find(android.view.View view, String value) {
        if (view instanceof android.widget.TextView && value.contentEquals(((android.widget.TextView)view).getText())) return view;
        if (view instanceof android.view.ViewGroup) {
            android.view.ViewGroup group=(android.view.ViewGroup)view;
            for(int i=0;i<group.getChildCount();i++){android.view.View found=find(group.getChildAt(i),value);if(found!=null)return found;}
        }
        return null;
    }
    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            if(arguments!=null && "ime_settings".equals(arguments.getString("scenario"))){
                if(!"ranchu".equals(android.os.Build.HARDWARE)&&!"goldfish".equals(android.os.Build.HARDWARE))throw new AssertionError("emulator only");
                android.content.Context c=getTargetContext();OinInputService.setOptedIn(c,false);
                Activity settings=startActivitySync(new Intent(c,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                waitForIdleSync();
                runOnMainSync(()->{
                    android.view.View root=settings.getWindow().getDecorView();
                    android.view.View label=find(root,"使用 OIN 输入法（可选）");
                    if(label==null||find(root,"启用输入法")==null||find(root,"选择输入法")==null)throw new AssertionError("missing optional controls");
                    android.view.ViewGroup row=(android.view.ViewGroup)label.getParent();
                    android.widget.Switch toggle=(android.widget.Switch)row.getChildAt(1);
                    if(toggle.isChecked()||OinInputService.optedIn(c))throw new AssertionError("must default off");
                    toggle.setChecked(true);if(!OinInputService.optedIn(c))throw new AssertionError("enable not saved");
                    toggle.setChecked(false);if(OinInputService.optedIn(c))throw new AssertionError("disable not saved");
                });
                runOnMainSync(settings::finish);result.putString("result","optional_ime_settings_passed");finish(Activity.RESULT_OK,result);return;
            }
            if(arguments!=null && "ime_search_fixture".equals(arguments.getString("scenario"))){
                if(!"ranchu".equals(android.os.Build.HARDWARE)&&!"goldfish".equals(android.os.Build.HARDWARE))throw new AssertionError("emulator only");
                android.content.Context c=getTargetContext();
                android.app.UiAutomation automation=getUiAutomation(android.app.UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
                for(String command:new String[]{"settings put secure accessibility_enabled 0","settings put secure enabled_accessibility_services ''","settings put secure enabled_accessibility_services com.feifei.phonebridge/com.feifei.phonebridge.BridgeAccessibilityService","settings put secure accessibility_enabled 1","ime enable com.feifei.phonebridge/.OinInputService","ime set com.feifei.phonebridge/.OinInputService","input keyevent 4"}){
                    try(android.os.ParcelFileDescriptor fd=automation.executeShellCommand(command);java.io.FileInputStream in=new java.io.FileInputStream(fd.getFileDescriptor())){while(in.read()!=-1){}}
                }
                BridgeState.initialize(c);BridgeState.setPaused(c,false);OinInputService.setOptedIn(c,true);
                c.startActivity(new Intent().setComponent(new android.content.ComponentName("com.taobao.idlefish","com.taobao.idlefish.FixtureActivity")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                long until=android.os.SystemClock.elapsedRealtime()+12000;
                while(android.os.SystemClock.elapsedRealtime()<until){
                    BridgeAccessibilityService bridge=BridgeAccessibilityService.getInstance();
                    if(bridge!=null&&"com.taobao.idlefish".equals(bridge.currentActivePackage()))break;
                    Thread.sleep(100);
                }
                Thread.sleep(1500);
                org.json.JSONObject request=new org.json.JSONObject().put("action","set_node_text").put("package","com.taobao.idlefish")
                    .put("selector",new org.json.JSONObject().put("kind","class").put("value","android.widget.EditText")).put("value","桌面收纳");
                ActionDispatcher dispatcher=new ActionDispatcher(c);
                org.json.JSONObject first=dispatcher.dispatch(request,12000);
                if(!first.optBoolean("success")||!"ime_search_readback_verified".equals(first.optString("message"))){
                    BridgeAccessibilityService b=BridgeAccessibilityService.getInstance();
                    android.view.accessibility.AccessibilityNodeInfo root=b==null?null:b.getRootInActiveWindow();
                    android.view.accessibility.AccessibilityNodeInfo focused=root==null?null:root.findFocus(android.view.accessibility.AccessibilityNodeInfo.FOCUS_INPUT);
                    throw new AssertionError("IME integration: "+first+" env="+XianyuExecutionEnvironment.capture(c).state+" pkg="+(root==null?"null":root.getPackageName())+" focused="+(focused==null?"null":focused.getClassName()+":"+focused.isFocused())+" identified="+(root!=null&&b.focusedSearchEditor(root.getWindowId()))+" selected="+OinInputService.selected(c)+" opted="+OinInputService.optedIn(c)+" paused="+BridgeState.PAUSED.get());
                }
                org.json.JSONObject same=dispatcher.dispatch(request,12000);
                if(!same.optBoolean("success")||same.optBoolean("may_have_written"))throw new AssertionError("same text repeated: "+same);
                OinInputService.setOptedIn(c,false);request.put("value","书签");
                org.json.JSONObject fallback=dispatcher.dispatch(request,12000);
                if(!fallback.optBoolean("success")||!"node_text_readback_verified".equals(fallback.optString("message")))throw new AssertionError("original path: "+fallback);
                BridgeState.setPaused(c,true);OinInputService.setOptedIn(c,true);request.put("value","台灯");
                org.json.JSONObject paused=dispatcher.dispatch(request,12000);
                if(paused.optBoolean("success")||!"bridge_paused".equals(paused.optString("message")))throw new AssertionError("pause gate");
                OinInputService.setOptedIn(c,false);
                result.putString("result","emulator_fixture_ime_chinese_readback_idempotence_optional_off_and_pause_passed");finish(Activity.RESULT_OK,result);return;
            }
            if(arguments!=null && "native_inventory".equals(arguments.getString("scenario"))) {
                NativeSellerClient probe=new NativeSellerClient(getTargetContext());
                try {
                    org.json.JSONObject one=probe.inventory(),two=probe.inventory();
                    result.putString("result","two_bound_inventory_reads_passed");
                    result.putInt("jobs",two.getJSONArray("jobs").length());
                    result.putInt("bytes",two.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length);
                    finish(Activity.RESULT_OK,result);return;
                }catch(Throwable failure){
                    result.putString("failure",failure.toString());
                    if(failure.getCause()!=null)result.putString("cause",failure.getCause().toString());
                    finish(Activity.RESULT_CANCELED,result);return;
                }finally{probe.close();}
            }
            if (arguments != null && "diagnostics".equals(arguments.getString("scenario"))) {
                android.content.Context c=getTargetContext();
                org.json.JSONObject d=new org.json.JSONObject();
                d.put("error",CloudState.lastErrorCode(c));d.put("supervisor_reason",CloudState.supervisorReason(c));
                d.put("reconnect_reason",CloudState.reconnectReason(c));d.put("last_retire_reason",CloudState.lastRetireReason(c));
                d.put("last_retire_at",CloudState.lastRetireAt(c));d.put("heartbeat_sent_at",CloudState.lastHeartbeatSentAt(c));
                d.put("heartbeat_ack_at",CloudState.lastHeartbeatAcknowledgedAt(c));d.put("reconnect_attempt",CloudState.reconnectAttempt(c));
                d.put("recovery_failure",CloudState.recoveryFailureStage(c));d.put("recovery_outcome",CloudState.recoveryOutcome(c));
                d.put("outbox_stage",CloudState.outboxFailureStage(c));d.put("previous_exit",CloudState.previousProcessExit(c));
                result.putString("diagnostics",d.toString());finish(Activity.RESULT_OK,result);return;
            }
            if (arguments != null && "auth".equals(arguments.getString("scenario"))) {
                Activity account = startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                waitForIdleSync();
                runOnMainSync(() -> {
                    android.view.View login=find(account.getWindow().getDecorView(),"登录");
                    android.view.View register=find(account.getWindow().getDecorView(),"注册");
                    if(find(account.getWindow().getDecorView(),"登录并绑定本机")!=null || find(account.getWindow().getDecorView(),"登录账号")!=null)
                        throw new AssertionError("Duplicate login entry must be removed");
                    if(login==null||register==null||login.getParent()!=register.getParent()||!register.performClick())throw new AssertionError("Both account entries must exist");
                });
                waitForIdleSync();
                getUiAutomation().waitForIdle(500,5000);
                android.view.accessibility.AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
                if(root==null||root.findAccessibilityNodeInfosByText("注册 OIN 账号").isEmpty()) {
                    android.graphics.Bitmap failed=getUiAutomation().takeScreenshot();
                    try(java.io.FileOutputStream out=new java.io.FileOutputStream(new java.io.File(getTargetContext().getExternalFilesDir(null),"auth-failure.png"))){failed.compress(android.graphics.Bitmap.CompressFormat.PNG,100,out);}
                    throw new AssertionError("Native registration did not open: "+root);
                }
                boolean clicked=false;
                for(android.view.accessibility.AccessibilityNodeInfo n:root.findAccessibilityNodeInfosByText("注册"))
                    if("注册".contentEquals(n.getText()==null?"":n.getText()))clicked=n.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK);
                if(!clicked)throw new AssertionError("Registration submit missing");
                waitForIdleSync();
                getUiAutomation().waitForIdle(500,5000);
                root=getUiAutomation().getRootInActiveWindow();
                if(root.findAccessibilityNodeInfosByText("用户名需为3–48位").isEmpty())throw new AssertionError("Missing validation feedback");
                android.graphics.Bitmap image=getUiAutomation().takeScreenshot();
                try(java.io.FileOutputStream out=new java.io.FileOutputStream(new java.io.File(getTargetContext().getExternalFilesDir(null),"auth-entry.png"))){image.compress(android.graphics.Bitmap.CompressFormat.PNG,100,out);}
                image.recycle();runOnMainSync(account::finish);
                result.putString("result","login_register_entries_and_native_validation_passed");finish(Activity.RESULT_OK,result);return;
            }
            Intent intent = new Intent(getTargetContext(), NativeTaskActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Activity activity = startActivitySync(intent);
            waitForIdleSync();
            Thread.sleep(1500);
            runOnMainSync(() -> {
                if (activity.isFinishing() || activity.isDestroyed())
                    throw new AssertionError("Task center exited after launch");
            });
            runOnMainSync(() -> {
                if(find(activity.getWindow().getDecorView(),"预热")==null)throw new AssertionError("Preheat entry unavailable");
                for(String label:new String[]{"任务记录","商品数据","日常维护","查看故障与记录"}) {
                    android.view.View old=find(activity.getWindow().getDecorView(),label);
                    if(old!=null&&old.isShown())throw new AssertionError("Removed customer entry still shown: "+label);
                }
            });
            waitForIdleSync();
            android.graphics.Bitmap screen = getUiAutomation().takeScreenshot();
            if (screen == null) throw new AssertionError("Task center screenshot unavailable");
            java.io.File shot = new java.io.File(getTargetContext().getExternalFilesDir(null), "task-center.png");
            try (java.io.FileOutputStream out = new java.io.FileOutputStream(shot)) {
                screen.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out);
            }
            screen.recycle();
            runOnMainSync(activity::finish);
            waitForIdleSync();
            result.putString("result", "simple_preheat_launch_without_history_or_metrics_passed");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable failure) {
            result.putString("failure", failure.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}
