package com.feifei.phonebridge;

import android.os.Handler;
import android.os.Looper;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.CopyOnWriteArrayList;

/** Shares actual task state transitions with visible UI; no timer or device inspection. */
final class TaskUiUpdates {
 interface Listener { void onTaskState(String device,JSONObject inventory); }
 private static final CopyOnWriteArrayList<Listener> listeners=new CopyOnWriteArrayList<>();
 private static final Handler main=new Handler(Looper.getMainLooper());
 static void subscribe(Listener l){listeners.addIfAbsent(l);}
 static void unsubscribe(Listener l){listeners.remove(l);}
 static String stateKey(JSONObject inventory){
  StringBuilder key=new StringBuilder();JSONArray jobs=inventory.optJSONArray("jobs");
  if(jobs!=null)for(int i=0;i<jobs.length();i++){JSONObject j=jobs.optJSONObject(i);if(j!=null)key.append(j.optString("id")).append('|').append(j.optString("status")).append('|').append(j.optString("control_state")).append('|').append(j.optString("error_code")).append('|').append(j.optBoolean("requires_manual")).append(';');}
  return key.toString();
 }
 static void publish(String device,JSONObject inventory){
  if(listeners.isEmpty())return;
  main.post(()->{for(Listener l:listeners)l.onTaskState(device,inventory);});
 }
}
