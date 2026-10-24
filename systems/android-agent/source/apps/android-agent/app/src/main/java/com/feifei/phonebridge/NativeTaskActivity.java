package com.feifei.phonebridge;

import android.app.*;
import android.content.Intent;
import android.graphics.Color;
import android.os.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.concurrent.*;

/** Two everyday actions. Connection setup is a separate, optional page. */
public final class NativeTaskActivity extends Activity {
 private final Handler main=new Handler(Looper.getMainLooper());
 private final ExecutorService io=Executors.newSingleThreadExecutor();
 private NativeSellerClient client;
 private TextView connection,result,message;
 private LinearLayout currentPanel,controls;
 private Button preheat,maintain,resume,stop,check;
 private JSONObject inventory,job;
 private boolean busy,visible,pauseOnReturn=true,recovering;
 private long generation;
 private int loadFailures;
 private static final String PREHEAT="daily_preheat_v5",MAINTAIN="daily_maintenance_v5";
 private final TaskUiUpdates.Listener taskUpdates=(device,data)->{
  if(visible&&!busy&&device.equals(CloudState.deviceId(this)))render(data);
 };
 private final android.content.SharedPreferences.OnSharedPreferenceChangeListener connectionUpdates=(prefs,key)->{
  if(visible&&java.util.Arrays.asList("connection_state","last_heartbeat_ack_at","service_state","device_state_status").contains(key))main.post(()->{if(visible)renderConnection();});
 };
 @Override public void onCreate(Bundle saved){super.onCreate(saved);client=new NativeSellerClient(this);ControlService.requestStart(this);View root=content();setContentView(root);MobileUi.safeArea(this,root);}
 private View content(){
  LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.rgb(246,248,248));
  LinearLayout bar=new LinearLayout(this);bar.setGravity(Gravity.CENTER_VERTICAL);bar.setPadding(dp(20),0,dp(12),0);
  TextView title=text("OIN",17);title.setTypeface(null,android.graphics.Typeface.BOLD);bar.addView(title,new LinearLayout.LayoutParams(0,-2,1));
  Button settings=button("设置",false);settings.setOnClickListener(v->startActivity(new Intent(this,MainActivity.class)));bar.addView(settings);root.addView(bar,new LinearLayout.LayoutParams(-1,dp(48)));
  ScrollView scroll=new ScrollView(this);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(dp(20),dp(12),dp(20),dp(20));scroll.addView(body);
  connection=text("正在连接…",12);body.addView(connection);
  LinearLayout actions=panel(body);preheat=button("开始预热",true);maintain=button("开始日常维护",true);
  actions.addView(preheat,new LinearLayout.LayoutParams(-1,dp(48)));LinearLayout.LayoutParams gap=new LinearLayout.LayoutParams(-1,dp(48));gap.topMargin=dp(12);actions.addView(maintain,gap);
  preheat.setOnClickListener(v->begin(PREHEAT));maintain.setOnClickListener(v->begin(MAINTAIN));
  currentPanel=panel(body);result=text("",14);currentPanel.addView(result);controls=new LinearLayout(this);controls.setGravity(Gravity.CENTER_VERTICAL);currentPanel.addView(controls);
  resume=button("继续",false);stop=button("结束",false);controls.addView(resume);LinearLayout.LayoutParams space=new LinearLayout.LayoutParams(-2,dp(44));space.leftMargin=dp(12);controls.addView(stop,space);
  resume.setOnClickListener(v->resumeTask());stop.setOnClickListener(v->stopTask());currentPanel.setVisibility(View.GONE);
  message=text("",12);body.addView(message);check=button("核对上次启动",false);check.setOnClickListener(v->submit(new JSONObject()));body.addView(check);check.setVisibility(View.GONE);
  TextView version=text(BuildConfig.VERSION_NAME,11);version.setPadding(dp(20),dp(8),dp(20),dp(12));root.addView(version);renderConnection();setBusy(false);return root;
 }
 private LinearLayout panel(LinearLayout parent){LinearLayout v=new LinearLayout(this);v.setOrientation(LinearLayout.VERTICAL);v.setPadding(dp(16),dp(16),dp(16),dp(16));v.setBackground(MobileUi.surface(Color.WHITE,dp(12)));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(16);parent.addView(v,lp);return v;}
 private TextView text(String value,int size){TextView v=new TextView(this);v.setText(value);v.setTextSize(size);v.setTextColor(Color.rgb(51,65,68));v.setPadding(0,dp(3),0,dp(3));return v;}
 private Button button(String value,boolean primary){Button b=new Button(this);b.setText(value);b.setTextSize(14);b.setAllCaps(false);b.setMinHeight(dp(44));b.setMinimumHeight(dp(44));b.setMinWidth(dp(60));b.setMinimumWidth(dp(60));b.setPadding(dp(14),dp(8),dp(14),dp(8));b.setTextColor(primary?Color.WHITE:Color.rgb(22,138,128));b.setBackground(MobileUi.surface(primary?Color.rgb(22,138,128):Color.TRANSPARENT,dp(10)));return b;}
 private int dp(int v){return MobileUi.dp(this,v);}
 private boolean active(){return job!=null&&java.util.Arrays.asList("queued","running","waiting","unknown").contains(job.optString("status"));}
 private JSONObject plan(String id){JSONArray list=inventory==null?null:inventory.optJSONArray("builtins");if(list!=null)for(int i=0;i<list.length();i++){JSONObject p=list.optJSONObject(i);if(p!=null&&id.equals(p.optString("id"))&&p.optBoolean("available"))return p;}return null;}
 private boolean currentPlan(){for(String id:new String[]{PREHEAT,MAINTAIN}){JSONObject p=plan(id);if(p!=null&&job!=null&&p.optString("recipe_id").equals(job.optString("recipe_id")))return true;}return false;}
 private void renderConnection(){String name=DevicePreferences.displayName(this);boolean connected=ControlService.isCloudChannelFresh(this);String label=name+" · "+(connected?(BridgeAccessibilityService.getInstance()!=null?"已连接":"无障碍连接恢复中"):"正在连接");if(!label.contentEquals(connection.getText()))connection.setText(label);}
 private void setBusy(boolean value){busy=value;if(preheat==null)return;boolean canStart=!value&&!recovering&&!active()&&!client.pending();preheat.setEnabled(canStart&&plan(PREHEAT)!=null);maintain.setEnabled(canStart&&plan(MAINTAIN)!=null);preheat.setAlpha(preheat.isEnabled()?1f:.45f);maintain.setAlpha(maintain.isEnabled()?1f:.45f);resume.setEnabled(!value&&!recovering);stop.setEnabled(!value);check.setEnabled(!value);check.setVisibility(client.pending()?View.VISIBLE:View.GONE);}
 private void render(JSONObject data){inventory=data;job=null;JSONArray jobs=data.optJSONArray("jobs");if(jobs!=null){for(int i=0;i<jobs.length();i++){JSONObject j=jobs.optJSONObject(i);if(j!=null&&java.util.Arrays.asList("queued","running","waiting","unknown").contains(j.optString("status"))){job=j;break;}}if(job==null&&jobs.length()>0)job=jobs.optJSONObject(0);}
  currentPanel.setVisibility(job==null?View.GONE:View.VISIBLE);if(job!=null){boolean running=active();String mode=job.optString("control_state"),state=job.optString("status");String label=running?("paused".equals(mode)?"已暂停":"pausing".equals(mode)?"正在暂停":"stopping".equals(mode)?"正在结束":"unknown".equals(state)?"结果待核对":"执行中"):"succeeded".equals(state)?"已完成":"cancelled".equals(state)?"已结束":"未完成";result.setText(job.optString("name","本次任务")+" · "+label);String reason=job.optString("error_message");if(running&&!reason.isEmpty()&&"paused".equals(mode))result.append("\n"+reason);controls.setVisibility(running?View.VISIBLE:View.GONE);resume.setVisibility(running&&"paused".equals(mode)&&currentPlan()?View.VISIBLE:View.GONE);stop.setVisibility(running&&!"stopping".equals(mode)?View.VISIBLE:View.GONE);}
  if(!recovering)message.setText("");renderConnection();setBusy(false);
 }
 private void load(){if(busy)return;setBusy(true);io.execute(()->{try{JSONObject data=client.inventory();if(pauseOnReturn){boolean pausedAny=false;JSONArray list=data.optJSONArray("jobs");if(list!=null)for(int i=0;i<list.length();i++){JSONObject j=list.optJSONObject(i);if(j!=null&&java.util.Arrays.asList("queued","running","waiting").contains(j.optString("status"))&&"active".equals(j.optString("control_state"))){client.control(j.getString("id"),"pause");pausedAny=true;}}pauseOnReturn=false;if(pausedAny)data=client.inventory();}JSONObject display=data;show(()->{loadFailures=0;render(display);});}catch(Exception e){android.util.Log.w("OIN-Task","Inventory load failed",e);show(()->{loadFailures++;setBusy(false);message.setText(e instanceof NativeSellerClient.Rejected?"连接凭据不可用，请检查设置":"连接暂未恢复，正在重试");if(!(e instanceof NativeSellerClient.Rejected)&&loadFailures<=3)main.postDelayed(()->{if(visible)load();},loadFailures*2000L);});}});}
 private boolean ready(Runnable retry){if(recovering)return false;if(!BridgeState.isAccessibilityEnabled(this)){settingsPrompt(false);return false;}if(!AccessibilityRecovery.connected()){recovering=true;long attempt=++generation;message.setText("正在恢复无障碍连接…");setBusy(false);AccessibilityRecovery.await(this,main,()->{if(!recovering||attempt!=generation)return;recovering=false;setBusy(false);if(visible)retry.run();},()->{if(!recovering||attempt!=generation)return;recovering=false;setBusy(false);settingsPrompt(BridgeState.isAccessibilityEnabled(this));});return false;}if(getPackageManager().getLaunchIntentForPackage("com.taobao.idlefish")==null){message.setText("请先安装闲鱼");return false;}return true;}
 private void settingsPrompt(boolean enabled){new AlertDialog.Builder(this).setTitle(enabled?"连接暂未恢复":"需要开启无障碍服务").setMessage(enabled?"开关已开启。可稍后重试，或检查系统服务连接；任务进度保留。":"请在系统设置开启 OIN 无障碍服务。").setPositiveButton(enabled?"检查连接":"去开启",(d,w)->startActivity(new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS))).setNegativeButton("取消",null).show();}
 private void begin(String id){if(busy||active())return;if(client.pending()){submit(new JSONObject());return;}if(!ready(()->begin(id)))return;if(plan(id)==null){message.setText("功能暂未就绪，请稍后重试");return;}try{submit(new JSONObject().put("builtin_id",id));}catch(JSONException e){message.setText("无法开始，请重试");}}
 private boolean openXianyu(){Intent intent=getPackageManager().getLaunchIntentForPackage("com.taobao.idlefish");if(intent==null){message.setText("请先安装闲鱼");return false;}startActivity(intent);return true;}
 private void submit(JSONObject payload){if(busy)return;if(!ready(()->submit(payload)))return;setBusy(true);message.setText("正在开始…");io.execute(()->{try{JSONObject accepted=client.start(payload);show(()->{setBusy(false);if(java.util.Arrays.asList("queued","running","waiting").contains(accepted.optString("status"))){if(openXianyu())finish();}else load();});}catch(Exception e){show(()->{setBusy(false);message.setText("启动尚未确认，请核对，避免重复开始");});}});}
 private void resumeTask(){if(busy||!active()||!ready(this::resumeTask))return;String id=job.optString("id");if(!openXianyu())return;setBusy(true);io.execute(()->{try{client.control(id,"resume");show(this::finish);}catch(Exception e){show(()->{setBusy(false);message.setText("继续未确认，请返回核对");startActivity(new Intent(this,NativeTaskActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));});}});}
 private void stopTask(){recovering=false;generation++;if(busy||!active())return;String id=job.optString("id");boolean unknown="unknown".equals(job.optString("status"));setBusy(true);io.execute(()->{try{if(unknown)client.cancel(id);else client.control(id,"stop");show(()->{setBusy(false);load();});}catch(Exception e){show(()->{setBusy(false);message.setText("结束未确认，正在重新核对");load();});}});}
 private void show(Runnable r){main.post(()->{if(!isFinishing()&&!isDestroyed())r.run();});}
 @Override protected void onResume(){super.onResume();visible=true;pauseOnReturn=true;TaskUiUpdates.subscribe(taskUpdates);getSharedPreferences("oin_cloud_state",MODE_PRIVATE).registerOnSharedPreferenceChangeListener(connectionUpdates);renderConnection();load();}
 @Override protected void onPause(){visible=false;recovering=false;generation++;TaskUiUpdates.unsubscribe(taskUpdates);getSharedPreferences("oin_cloud_state",MODE_PRIVATE).unregisterOnSharedPreferenceChangeListener(connectionUpdates);super.onPause();}
 @Override protected void onDestroy(){TaskUiUpdates.unsubscribe(taskUpdates);client.close();io.shutdownNow();main.removeCallbacksAndMessages(null);super.onDestroy();}
}
