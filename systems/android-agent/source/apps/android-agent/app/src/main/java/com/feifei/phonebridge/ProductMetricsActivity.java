package com.feifei.phonebridge;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Color;
import android.widget.*;
import org.json.*;
import java.util.concurrent.*;

/** Bound-device read-only product results; no synthetic totals or automatic edits. */
public final class ProductMetricsActivity extends Activity {
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private NativeSellerClient client;private LinearLayout rows;private TextView state;
    @Override public void onCreate(Bundle saved){super.onCreate(saved);client=new NativeSellerClient(this);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(dp(20),dp(16),dp(20),dp(16));root.setBackgroundColor(Color.rgb(246,248,249));
        LinearLayout bar=new LinearLayout(this);Button back=new Button(this);back.setText("返回");back.setOnClickListener(v->finish());back.setPadding(dp(12),dp(8),dp(12),dp(8));back.setMinimumHeight(dp(44));LinearLayout.LayoutParams backSpace=new LinearLayout.LayoutParams(-2,-2);backSpace.rightMargin=dp(12);backSpace.bottomMargin=dp(8);bar.addView(back,backSpace);TextView title=label("商品数据",21);bar.addView(title,new LinearLayout.LayoutParams(0,-2,1f));root.addView(bar);
        state=label("正在读取…",14);root.addView(state);ScrollView scroll=new ScrollView(this);rows=new LinearLayout(this);rows.setOrientation(LinearLayout.VERTICAL);scroll.addView(rows);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1f));setContentView(root);MobileUi.safeArea(this,root);
        io.execute(()->{try{JSONObject data=client.metrics();main.post(()->{if(isFinishing()||isDestroyed())return;render(data);});}catch(Exception e){main.post(()->{if(!isFinishing()&&!isDestroyed())state.setText("暂时无法读取，请稍后重新打开");});}});
    }
    private TextView label(String text,int size){TextView v=new TextView(this);v.setText(text);v.setTextSize(size);v.setTextColor(Color.rgb(35,56,60));v.setPadding(dp(12),dp(8),dp(12),dp(8));return v;}
    private void render(JSONObject data){String status=data.optString("state");state.setText(status.equals("empty_goods")?"当前没有在售商品":status.equals("not_collected")?"运行日常维护后，在这里查看商品数据":status.equals("captured")?"曝光下降的商品会优先显示":"本次未读到可确认的商品数据");
        JSONArray list=data.optJSONArray("products");rows.removeAllViews();if(list==null)return;
        for(int n=0;n<list.length();n++){JSONObject p=list.optJSONObject(n);if(p==null)continue;LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(12),dp(14),dp(12),dp(14));card.setBackground(MobileUi.surface(Color.WHITE,dp(16)));TextView name=label(p.optString("title"),17);card.addView(name);
            String scope=p.optString("scope");String amount=(scope.equals("daily")?"今日曝光 ":scope.equals("cumulative")?"累计曝光 ":"曝光 ")+p.optInt("exposure");if(p.has("previous_exposure"))amount+=" · 昨日同时段 "+p.optInt("previous_exposure");card.addView(label(amount,14));TextView advice=label(p.optString("advice"),14);if(p.optString("trend").equals("down"))advice.setTextColor(Color.rgb(165,83,36));card.addView(advice);
            long captured=p.optLong("captured_at");card.addView(label("采集："+android.text.format.DateFormat.format("MM-dd HH:mm",captured*1000),12));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.bottomMargin=dp(14);rows.addView(card,lp);}
    }
    private int dp(int value){return MobileUi.dp(this,value);}
    @Override protected void onDestroy(){if(client!=null)client.close();io.shutdownNow();main.removeCallbacksAndMessages(null);super.onDestroy();}
}
