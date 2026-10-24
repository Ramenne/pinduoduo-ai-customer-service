package com.feifei.phonebridge;
import android.app.*;
import android.content.*;
import android.os.*;
import android.widget.*;
import java.util.concurrent.*;

/** A real notification click gives a foreground Activity for returning to Xianyu. */
public final class TaskResumeActivity extends Activity {
 private final ExecutorService io=Executors.newSingleThreadExecutor();private final Handler main=new Handler(Looper.getMainLooper());private NativeSellerClient client;
 @Override public void onCreate(Bundle saved){super.onCreate(saved);TextView text=new TextView(this);text.setText("正在继续任务…");text.setTextSize(18);text.setPadding(32,48,32,32);setContentView(text);MobileUi.safeArea(this,text);client=new NativeSellerClient(this);String job=getIntent().getStringExtra("job_id");Intent xianyu=getPackageManager().getLaunchIntentForPackage("com.taobao.idlefish");if(job==null||xianyu==null){text.setText("无法打开闲鱼，请到任务中心检查");return;}if(BridgeState.PAUSED.get()){text.setText("手机控制已暂停，请在连接设置中恢复");return;}continueWhenReady(text,job,xianyu);}
 private void continueWhenReady(TextView text,String job,Intent xianyu){
 if(!BridgeState.isAccessibilityEnabled(this)){text.setText("请在 OIN 设置中开启无障碍服务");return;}
 if(!AccessibilityRecovery.connected()){text.setText("正在恢复无障碍连接…");AccessibilityRecovery.await(this,main,()->continueWhenReady(text,job,xianyu),()->text.setText(BridgeState.isAccessibilityEnabled(this)?"开关已开启，连接暂未恢复，请回 OIN 重试":"请在 OIN 设置中开启无障碍服务"));return;}
 if(BridgeState.PAUSED.get()){text.setText("手机控制已暂停，请在连接设置中恢复");return;}
 startActivity(xianyu);io.execute(()->{try{
 org.json.JSONObject inventory=client.inventory();org.json.JSONArray plans=inventory.optJSONArray("builtins"),jobs=inventory.optJSONArray("jobs");java.util.Set<String> recipes=new java.util.HashSet<>();
 if(plans!=null)for(int i=0;i<plans.length();i++){org.json.JSONObject p=plans.optJSONObject(i);if(p!=null&&java.util.Arrays.asList("daily_preheat_v5","daily_maintenance_v5").contains(p.optString("id")))recipes.add(p.optString("recipe_id"));}
 boolean current=false;if(jobs!=null)for(int i=0;i<jobs.length();i++){org.json.JSONObject j=jobs.optJSONObject(i);if(j!=null&&job.equals(j.optString("id"))&&recipes.contains(j.optString("recipe_id")))current=true;}
 if(!current)throw new java.io.IOException("legacy_plan_requires_restart");
 client.control(job,"resume");main.post(()->{if(isFinishing()||isDestroyed())return;finish();});}catch(Exception error){main.post(()->{if(isFinishing()||isDestroyed())return;text.setText("继续未确认，请回任务中心核对");Button back=new Button(this);back.setText("打开任务中心");back.setOnClickListener(v->{startActivity(new Intent(this,NativeTaskActivity.class));finish();});LinearLayout page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);TextView message=new TextView(this);message.setText("继续未确认，请回任务中心核对");message.setPadding(32,48,32,32);page.addView(message);page.addView(back);setContentView(page);MobileUi.safeArea(this,page);});}});}
 @Override protected void onPause(){main.removeCallbacksAndMessages(null);super.onPause();}
 @Override protected void onDestroy(){if(client!=null)client.close();io.shutdownNow();main.removeCallbacksAndMessages(null);super.onDestroy();}
}
