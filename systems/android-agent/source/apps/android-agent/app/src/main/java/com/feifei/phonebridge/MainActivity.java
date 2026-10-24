package com.feifei.phonebridge;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ClipData;
import android.net.Uri;
import androidx.core.content.FileProvider;
import java.io.File;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.ScrollView;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Minimal customer-facing setup surface. Diagnostics and tests belong to the cloud console. */
public final class MainActivity extends Activity {
    private static final int BLUE = Color.rgb(22, 138, 128);
    private static final int TEXT = Color.rgb(36, 56, 59);
    private static final int MUTED = Color.rgb(112, 119, 128);
    private static final int SURFACE = Color.rgb(245, 247, 248);
    private static final int GOOD = Color.rgb(27, 166, 93);
    private static final long UI_REFRESH_MILLIS = 1_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService cloudActions = Executors.newSingleThreadExecutor();
    private final ExecutorService updateActions = Executors.newSingleThreadExecutor();
    private AppUpdater updater;
    private Button updateButton;
    private TextView updateStatus;
    private File pendingUpdate;
    private AppUpdater.Release availableUpdate;
    private boolean updateBusy, waitingInstallPermission;

    private final Runnable refreshTicker = new Runnable() {
        @Override
        public void run() {
            if (!uiVisible) {
                return;
            }
            refreshUi();
            handler.postDelayed(this, UI_REFRESH_MILLIS);
        }
    };

    private TextView accountName;
    private TextView deviceName;
    private TextView loginAction;
    private LinearLayout loginPanel;
    private EditText cloudIdentifier;
    private EditText cloudPassword;
    private Button bindButton;
    private Button registrationAction;
    private boolean editingAccount;
    private Switch accessibilitySwitch;
    private Switch autoConnectSwitch;
    private Switch inputMethodSwitch;
    private TextView inputMethodStatus;
    private TextView serviceStatus;
    private TextView kernelVersion;
    private TextView androidVersion;
    private TextView recommendation;
    private boolean updatingSwitches;
    private boolean uiVisible;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        updater = new AppUpdater(this);
        View content=buildContent();
        setContentView(content);
        MobileUi.safeArea(this,content);
        if (AgentRuntimePreferences.autoConnectEnabled(this)) {
            String serviceResult = ControlService.requestStart(this);
            if ("service_start_failed".equals(serviceResult)) {
                toast("自动连接服务启动失败");
            }
        }
        refreshUi();
        restoreUpdate();
    }

    @Override
    protected void onResume() {
        super.onResume();
        uiVisible = true;
        if(waitingInstallPermission && !updateBusy) {
            waitingInstallPermission=false;
            if(getPackageManager().canRequestPackageInstalls())restoreUpdate(true);
            else updateStatus.setText("未允许安装；已下载的更新仍保留，可再点安装");
        }
        if (AgentRuntimePreferences.autoConnectEnabled(this)) {
            ControlService.notifyAccessibilityActivity(this);
        }
        handler.removeCallbacks(refreshTicker);
        handler.post(refreshTicker);
    }

    @Override
    protected void onPause() {
        uiVisible = false;
        handler.removeCallbacks(refreshTicker);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        cloudActions.shutdownNow();
        if (updater != null) updater.close();
        updateActions.shutdownNow();
        super.onDestroy();
    }

    private View buildContent() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(SURFACE);

        LinearLayout header = new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);header.setPadding(dp(12),0,dp(16),0);
        Button back = new Button(this);back.setText("‹");back.setTextSize(20);back.setBackgroundColor(Color.TRANSPARENT);back.setOnClickListener(v->{if(!CloudState.deviceId(this).isEmpty())startActivity(new Intent(this,NativeTaskActivity.class));finish();});header.addView(back,new LinearLayout.LayoutParams(dp(44),dp(44)));
        header.addView(text("设置",16,TEXT));page.addView(header,new LinearLayout.LayoutParams(-1,dp(48)));
        LinearLayout content = new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(16),dp(8),dp(16),0);page.addView(content,new LinearLayout.LayoutParams(-1,-2));
        LinearLayout accountCard = surface(content);
        LinearLayout accountRow = horizontalRow(accountCard);
        accountName = text("未登录", 15, TEXT);
        accountRow.addView(accountName, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        loginAction = actionText("切换账号", view -> toggleLoginPanel());
        accountRow.addView(loginAction);

        LinearLayout deviceRow = horizontalRow(accountCard);
        TextView deviceLabel = text("设备名：", 15, MUTED);
        deviceRow.addView(deviceLabel);
        deviceName = text("", 15, MUTED);
        deviceRow.addView(deviceName, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        deviceRow.addView(actionText("修改", view -> showRenameDialog()));

        loginPanel = new LinearLayout(this);
        loginPanel.setOrientation(LinearLayout.VERTICAL);
        cloudIdentifier = input("用户名或邮箱", false);
        cloudIdentifier.setText(getSharedPreferences("account_onboarding", MODE_PRIVATE).getString("registered_username", ""));
        cloudPassword = input("密码", true);
        loginPanel.addView(cloudIdentifier);
        loginPanel.addView(cloudPassword);
        LinearLayout authActions = new LinearLayout(this);
        authActions.setOrientation(LinearLayout.HORIZONTAL);
        bindButton = primaryButton("登录", view -> runEnrollment());
        registrationAction = primaryButton("注册", view -> showRegistrationDialog());
        registrationAction.setTextColor(BLUE);
        registrationAction.setBackground(MobileUi.surface(SURFACE, dp(10)));
        LinearLayout.LayoutParams loginParams = new LinearLayout.LayoutParams(0, dp(48), 1f);
        loginParams.topMargin = dp(12);
        loginParams.rightMargin = dp(6);
        LinearLayout.LayoutParams registerParams = new LinearLayout.LayoutParams(0, dp(48), 1f);
        registerParams.topMargin = dp(12);
        registerParams.leftMargin = dp(6);
        authActions.addView(bindButton, loginParams);
        authActions.addView(registrationAction, registerParams);
        loginPanel.addView(authActions);
        accountCard.addView(loginPanel);

        LinearLayout switches = surface(content);
        accessibilitySwitch = switchRow(
                switches,
                "开启无障碍服务",
                (button, checked) -> {
                    if (updatingSwitches) {
                        return;
                    }
                    startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
                });
        autoConnectSwitch = switchRow(
                switches,
                "自动连接服务器",
                (button, checked) -> {
                    if (updatingSwitches) {
                        return;
                    }
                    String result = ControlService.setAutoConnectEnabled(this, checked);
                    if ("auto_connect_preference_failed".equals(result) ||
                            "service_start_failed".equals(result) ||
                            "service_stop_failed".equals(result)) {
                        toast("自动连接设置失败");
                    }
                    refreshUi();
                });

        LinearLayout inputMethod = surface(content);
        inputMethodSwitch = switchRow(inputMethod, "使用 OIN 输入法（可选）", (button, checked) -> {
            if(updatingSwitches)return;
            if(!OinInputService.setOptedIn(this,checked))toast("输入方式保存失败");
            refreshUi();
            if(!checked&&OinInputService.selected(this))showInputMethodPicker();
        });
        inputMethodStatus=text("",14,MUTED);inputMethod.addView(inputMethodStatus);
        inputMethod.addView(primaryButton("启用输入法", view -> {
            try{startActivity(new Intent(Settings.ACTION_INPUT_METHOD_SETTINGS));}
            catch(RuntimeException unavailable){toast("请在系统设置中启用 OIN 输入法");}
        }));
        inputMethod.addView(primaryButton("选择输入法", view -> showInputMethodPicker()));

        LinearLayout environment = surface(content);
        serviceStatus = statusRow(environment, "服务状态：");
        kernelVersion = statusRow(environment, "应用版本：");
        androidVersion = statusRow(environment, "安卓版本：");
        recommendation = statusRow(environment, "推荐：");
        LinearLayout updates = surface(content);
        updates.addView(text("当前安装：" + BuildConfig.VERSION_NAME + "（" + BuildConfig.VERSION_CODE + "）", 15, TEXT));
        updateStatus = text("最新版本：尚未检查", 15, MUTED);
        updates.addView(updateStatus);
        updateButton = primaryButton("检查更新", view -> {
            if(updateBusy)return;
            if(pendingUpdate!=null)restoreUpdate(true);
            else if(availableUpdate!=null)downloadUpdate(availableUpdate);
            else checkForUpdates();
        });
        updates.addView(updateButton);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(page);
        return scroll;
    }

    private void updateWorking(String message) {
        updateBusy=true;updateButton.setEnabled(false);updateButton.setAlpha(0.5f);updateStatus.setText(message);
    }
    private void updateIdle(String button,String message) {
        updateBusy=false;updateButton.setEnabled(true);updateButton.setAlpha(1f);updateButton.setText(button);updateStatus.setText(message);
    }
    private void restoreUpdate(){restoreUpdate(false);}
    private void restoreUpdate(boolean install) {
        if(updateBusy)return;
        updateWorking("正在核对已下载的更新…");
        updateActions.execute(()->{
            try {
                AppUpdater.Release saved=updater.restore();
                updateUi(()->{
                    if(saved==null){pendingUpdate=null;updateIdle("检查更新","尚未下载新版");return;}
                    availableUpdate=saved;pendingUpdate=updater.pendingFile(saved);
                    updateIdle("安装新版","已下载 "+saved.version+"，可继续安装");
                    if(install)installUpdate();
                });
            }catch(Exception failure){updateUi(()->{pendingUpdate=null;availableUpdate=null;updateIdle("检查更新","本地更新包不可用，请重新下载");});}
        });
    }
    private void checkForUpdates() {
        updateWorking("正在检查更新…");
        updateActions.execute(() -> {
            try {
                AppUpdater.Release release = updater.check();
                updateUi(() -> {
                    if(!AppUpdatePolicy.newer(BuildConfig.VERSION_CODE,release.code)) {
                        availableUpdate=null;updateIdle("重新检查","已是最新版本："+BuildConfig.VERSION_NAME);return;
                    }
                    availableUpdate=release;
                    updateIdle("下载新版","发现 "+release.version+"（"+release.code+"），约 "+release.size/1024+" KB");
                });
            }catch(Exception failure){updateUi(()->updateIdle("重试检查","暂时无法检查更新，请重试"));}
        });
    }
    private void downloadUpdate(AppUpdater.Release release) {
        updateWorking("正在下载 "+release.version+"，请稍候…");
        updateActions.execute(() -> {
            try {
                final int[] shown={-1};
                File file=updater.download(release,bytes->{int percent=(int)(100*bytes/release.size);
                    if(percent!=shown[0]){shown[0]=percent;updateUi(()->updateStatus.setText(percent==100?"下载完成，正在校验…":"正在下载 "+percent+"%"));}});
                updateUi(()->{pendingUpdate=file;updateIdle("安装新版","下载完成，校验通过，可继续安装");installUpdate();});
            }catch(Exception failure){
                updateUi(()->{
                    availableUpdate=null;
                    String code=failure.getMessage();
                    String message="下载中断，点重试重新下载";
                    if(code!=null && (code.contains("digest")||code.contains("signature")||code.contains("length")))message="更新包校验未通过，请重新下载";
                    else if(code!=null && code.contains("release_changed"))message="新版已变化，请重新检查";
                    updateIdle("重试检查",message);
                });
            }
        });
    }

    private void installUpdate() {
        if (pendingUpdate == null) return;
        updateStatus.setText("更新包已保留；请在系统安装器确认安装");
        if (!getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(this).setTitle("允许 OIN 安装更新")
                .setMessage("安卓要求你本人允许 OIN 安装应用更新。只用于此更新；允许后返回，会继续打开安装器。不需要卸载旧版。")
                .setNegativeButton("取消", null).setPositiveButton("打开系统设置", (dialog, which) -> {
                    try { waitingInstallPermission=true;startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:"+getPackageName()))); }
                    catch (RuntimeException unavailable) { toast("无法打开设置，请在系统设置中操作"); }
                }).show(); return;
        }
        try {
            Uri uri = FileProvider.getUriForFile(this, getPackageName()+".updates", pendingUpdate);
            Intent install = new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            install.setClipData(ClipData.newRawUri("oin-update", uri));
            startActivity(install);
        } catch (RuntimeException unavailable) { toast("无法打开系统安装器，请稍后重试"); }
    }

    private void updateUi(Runnable action) {
        handler.post(() -> { if (!isFinishing() && !isDestroyed()) action.run(); });
    }

    private LinearLayout surface(LinearLayout parent) {
        LinearLayout surface = new LinearLayout(this);
        surface.setOrientation(LinearLayout.VERTICAL);
        surface.setPadding(dp(18), dp(14), dp(18), dp(14));
        surface.setBackground(MobileUi.surface(Color.WHITE, dp(16)));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = dp(12);
        parent.addView(surface, params);
        return surface;
    }

    private LinearLayout horizontalRow(LinearLayout parent) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(8), 0, dp(8));
        parent.addView(row, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));
        return row;
    }

    private void showInputMethodPicker(){
        android.view.inputmethod.InputMethodManager manager=getSystemService(android.view.inputmethod.InputMethodManager.class);
        if(manager!=null)manager.showInputMethodPicker();
    }

    private Switch switchRow(
            LinearLayout parent,
            String label,
            android.widget.CompoundButton.OnCheckedChangeListener listener
    ) {
        LinearLayout row = horizontalRow(parent);
        TextView name = text(label, 17, TEXT);
        row.addView(name, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        Switch toggle = new Switch(this);
        toggle.setShowText(false);
        toggle.setOnCheckedChangeListener(listener);
        row.addView(toggle);
        return toggle;
    }

    private TextView statusRow(LinearLayout parent, String label) {
        LinearLayout row = horizontalRow(parent);
        TextView name = text(label, 15, MUTED);
        row.addView(name, new LinearLayout.LayoutParams(
                dp(98), LinearLayout.LayoutParams.WRAP_CONTENT));
        TextView value = text("", 15, TEXT);
        row.addView(value, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        return value;
    }

    private TextView actionText(String value, View.OnClickListener listener) {
        TextView action = text(value, 16, Color.rgb(49, 111, 214));
        action.setPadding(dp(10), dp(8), 0, dp(8));
        action.setOnClickListener(listener);
        return action;
    }

    private EditText input(String hint, boolean password) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setSingleLine(true);
        input.setTextSize(15);
        input.setInputType(password
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        params.topMargin = dp(4);
        input.setLayoutParams(params);
        return input;
    }

    private Button primaryButton(String value, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setTextColor(Color.WHITE);
        button.setBackground(MobileUi.surface(BLUE,dp(10)));
        button.setStateListAnimator(null);
        button.setOnClickListener(listener);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(48));
        params.topMargin = dp(8);
        button.setLayoutParams(params);
        return button;
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(Math.min(size,15));
        view.setTextColor(color);
        return view;
    }

    private void refreshUi() {
        boolean bound = CloudState.isBound(this) && !CloudState.isRevoked(this);
        String account = CloudState.accountName(this);
        accountName.setText(bound && !account.isEmpty() ? account : "未登录");
        deviceName.setText(DevicePreferences.displayName(this));
        loginPanel.setVisibility(bound && !editingAccount ? View.GONE : View.VISIBLE);
        loginAction.setVisibility(bound ? View.VISIBLE : View.GONE);
        loginAction.setText(editingAccount ? "取消切换" : "切换账号");

        updatingSwitches = true;
        boolean accessibilityEnabled = BridgeState.isAccessibilityEnabled(this);
        boolean autoConnectEnabled = AgentRuntimePreferences.autoConnectEnabled(this);
        accessibilitySwitch.setChecked(accessibilityEnabled);
        autoConnectSwitch.setChecked(autoConnectEnabled);
        boolean inputOpted=OinInputService.optedIn(this);
        inputMethodSwitch.setChecked(inputOpted);
        String inputStatus=!inputOpted?"已关闭，使用原有输入方式":
                !OinInputService.enabled(this)?"请先在系统设置中启用 OIN 输入法":
                !OinInputService.selected(this)?"已启用，请选择 OIN 输入法":"已就绪，仅用于闲鱼搜索输入";
        if(!inputStatus.contentEquals(inputMethodStatus.getText()))inputMethodStatus.setText(inputStatus);
        updatingSwitches = false;

        boolean cloudFresh = ControlService.isCloudChannelFresh(this);
        if (!bound) {
            setStatus(serviceStatus, "等待登录", MUTED);
        } else if (!autoConnectEnabled) {
            setStatus(serviceStatus, "自动连接已关闭", MUTED);
        } else if (cloudFresh) {
            setStatus(serviceStatus, "服务器连接正常", GOOD);
        } else if (ControlService.isRunning()) {
            setStatus(serviceStatus, "正在连接服务器", Color.rgb(196, 120, 18));
        } else {
            setStatus(serviceStatus, "服务器未连接", Color.rgb(196, 62, 62));
        }
        kernelVersion.setText(BuildConfig.VERSION_NAME + "（" + BuildConfig.VERSION_CODE + "）");
        androidVersion.setText(Build.VERSION.RELEASE + "（API " + Build.VERSION.SDK_INT + "）");
        if (!bound) {
            recommendation.setText("请先登录");
        } else if (!accessibilityEnabled) {
            recommendation.setText("请开启无障碍服务");
        } else if (BridgeAccessibilityService.getInstance() == null) {
            AccessibilityRecovery.request(this);
            recommendation.setText("无障碍已开启，正在恢复服务连接");
        } else if (!autoConnectEnabled) {
            recommendation.setText("建议开启自动连接服务器");
        } else if (!cloudFresh) {
            recommendation.setText("正在恢复云端连接");
        } else {
            recommendation.setText("当前使用环境正常");
        }
    }

    private void setStatus(TextView view, String value, int color) {
        view.setText(value);
        view.setTextColor(color);
    }

    private void toggleLoginPanel() {
        editingAccount = !editingAccount;
        cloudPassword.setText("");
        refreshUi();
    }

    private void showRegistrationDialog() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(20), dp(8), dp(20), dp(8));
        EditText username = input("用户名：3–48位字母、数字或 _ . -", false);
        EditText email = input("邮箱（可选）", false);
        email.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        EditText password = input("密码：12–128位", true);
        EditText confirmation = input("再次输入密码", true);
        TextView feedback = text("已有账号可返回登录", 14, MUTED);
        for (View field : new View[]{username, email, password, confirmation, feedback}) form.addView(field);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("注册 OIN 账号")
                .setView(form).setNegativeButton("返回登录", null).setPositiveButton("注册", null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String name = username.getText().toString().trim();
            String mail = email.getText().toString().trim();
            String secret = password.getText().toString();
            if (!name.matches("[A-Za-z0-9_.-]{3,48}")) { feedback.setText("用户名需为3–48位字母、数字或 _ . -"); return; }
            if (secret.length() < 12 || secret.length() > 128) { feedback.setText("密码需要12–128位"); return; }
            if (!secret.equals(confirmation.getText().toString())) { feedback.setText("两次密码不一致"); return; }
            if (!mail.isEmpty() && !android.util.Patterns.EMAIL_ADDRESS.matcher(mail).matches()) { feedback.setText("请输入有效邮箱，或留空"); return; }
            password.setText(""); confirmation.setText("");
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
            feedback.setText("正在保存账号…");
            cloudActions.execute(() -> {
                try {
                    String saved = new CloudApiClient(this).registerAccount(name, mail, secret);
                    getSharedPreferences("account_onboarding", MODE_PRIVATE).edit().putString("registered_username", saved).apply();
                    handler.post(() -> {
                        if (isFinishing() || isDestroyed()) return;
                        cloudIdentifier.setText(saved); cloudPassword.setText("");
                        dialog.dismiss(); toast("注册成功，请使用新账号登录"); refreshUi();
                    });
                } catch (Exception failure) {
                    String code = failure instanceof CloudApiClient.CloudApiException ? ((CloudApiClient.CloudApiException)failure).code : "cloud_network_error";
                    String message = "account_exists".equals(code) ? "该用户名或邮箱已注册，请返回登录" :
                        "invalid_account".equals(code) ? "账号信息不符合要求，请检查后重试" : "注册结果未确认，请先尝试用该账号登录；网络恢复后再处理";
                    handler.post(() -> { if (dialog.isShowing()) { feedback.setText(message); dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true); } });
                }
            });
        }));
        dialog.show();
    }

    private void runEnrollment() {
        String identifier = cloudIdentifier.getText().toString().trim();
        String password = cloudPassword.getText().toString();
        cloudPassword.setText("");
        if (identifier.isEmpty() || password.length() < 12) {
            toast("请输入有效账号和至少 12 位密码");
            return;
        }
        bindButton.setEnabled(false);
        registrationAction.setEnabled(false);
        loginAction.setEnabled(false);
        cloudActions.execute(() -> {
            String outcome;
            boolean success;
            try {
                CloudApiClient.EnrollmentResult result =
                        new CloudApiClient(this).enroll(identifier, password);
                outcome = "已绑定账号 " + result.accountName;
                success = true;
            } catch (CloudApiClient.CloudApiException expected) {
                CloudErrorCatalog.Entry explained = CloudErrorCatalog.explain(expected.code);
                outcome = explained.message;
                CloudState.recordConnectionError(this, expected.code);
                success = false;
            } catch (DeviceIdentity.DeviceIdentityException expected) {
                CloudErrorCatalog.Entry explained = CloudErrorCatalog.explain(expected.code);
                outcome = explained.message;
                CloudState.recordConnectionError(this, expected.code);
                success = false;
            } catch (Exception failure) {
                outcome = "登录失败，请稍后重试";
                success = false;
            }
            boolean completed = success;
            String message = outcome;
            handler.post(() -> {
                bindButton.setEnabled(true);
                registrationAction.setEnabled(true);
                loginAction.setEnabled(true);
                if (!isFinishing()) {
                    toast(message);
                    if (completed) {
                        editingAccount = false;
                        loginPanel.setVisibility(View.GONE);
                    }
                    refreshUi();
                }
            });
        });
    }

    private void showRenameDialog() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(DevicePreferences.displayName(this));
        input.setSelectAllOnFocus(true);
        int padding = dp(18);
        LinearLayout holder = new LinearLayout(this);
        holder.setPadding(padding, 0, padding, 0);
        holder.addView(input, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));
        new AlertDialog.Builder(this)
                .setTitle("修改设备名")
                .setView(holder)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存", (dialog, which) -> saveDeviceName(input))
                .show();
    }

    private void saveDeviceName(EditText input) {
        try {
            DevicePreferences.setDisplayName(this, input.getText().toString());
            ControlService.requestDeviceStateRefresh(this);
            refreshUi();
            toast("设备名已修改");
        } catch (RuntimeException invalid) {
            toast("设备名不能为空，且不能超过 80 个字");
        }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
