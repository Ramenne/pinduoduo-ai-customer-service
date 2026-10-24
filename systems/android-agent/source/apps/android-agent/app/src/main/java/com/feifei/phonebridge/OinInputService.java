package com.feifei.phonebridge;

import android.content.ComponentName;
import android.content.Context;
import android.inputmethodservice.InputMethodService;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.ExtractedText;
import android.view.inputmethod.ExtractedTextRequest;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import android.view.inputmethod.InputMethodInfo;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONException;
import org.json.JSONObject;

/** No input logging, learning, clipboard, shared token or automatic IME selection. */
public final class OinInputService extends InputMethodService {
    private static volatile OinInputService instance;
    private final Handler main=new Handler(Looper.getMainLooper());
    private long epoch;
    private boolean inputStarted;
    @Override public void onCreate(){super.onCreate();instance=this;}
    @Override public void onStartInput(EditorInfo info,boolean restarting){super.onStartInput(info,restarting);epoch++;inputStarted=true;}
    @Override public void onFinishInput(){inputStarted=false;epoch++;super.onFinishInput();}
    @Override public void onDestroy(){inputStarted=false;epoch++;if(instance==this)instance=null;main.removeCallbacksAndMessages(null);super.onDestroy();}
    @Override public boolean onEvaluateFullscreenMode(){return false;}
    @Override public View onCreateInputView(){
        LinearLayout panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);
        int padding=Math.round(16*getResources().getDisplayMetrics().density);panel.setPadding(padding,padding,padding,padding);
        TextView hint=new TextView(this);hint.setText("OIN 输入法\n可选的闲鱼搜索输入；其他应用请切换常用输入法。");hint.setTextSize(16);panel.addView(hint);
        Button switcher=new Button(this);switcher.setText("切换输入法");switcher.setOnClickListener(v->{InputMethodManager manager=getSystemService(InputMethodManager.class);if(manager!=null)manager.showInputMethodPicker();});panel.addView(switcher);
        return panel;
    }
    static boolean optedIn(Context context){return context.getSharedPreferences("oin_input",Context.MODE_PRIVATE).getBoolean("search_enabled",false);}
    static boolean setOptedIn(Context context,boolean enabled){return context.getSharedPreferences("oin_input",Context.MODE_PRIVATE).edit().putBoolean("search_enabled",enabled).commit();}

    static JSONObject inputSearch(Context context,String value,int window,long remaining) throws JSONException {
        if(Looper.myLooper()==Looper.getMainLooper())return outcome(false,false,"ime_requires_task_worker");
        if(!optedIn(context))return outcome(false,false,"ime_search_disabled");
        if(!selected(context))return outcome(false,false,"oin_input_method_not_selected");
        long budget=Math.min(10000L,Math.max(0,remaining));
        if(budget<1000)return outcome(false,false,"ime_search_budget_exhausted");
        long deadline=SystemClock.elapsedRealtime()+budget;
        Handler ui=new Handler(Looper.getMainLooper());
        AtomicBoolean cancelled=new AtomicBoolean(false),wrote=new AtomicBoolean(false);
        DescriptionInputPolicy.WriteFence fence=new DescriptionInputPolicy.WriteFence();
        AtomicReference<SearchInputPolicy.Result> result=new AtomicReference<>();
        CountDownLatch done=new CountDownLatch(1);
        Runnable operation=new Runnable(){public void run(){
            try{
                if(cancelled.get()||SystemClock.elapsedRealtime()>=deadline)return;
                OinInputService service=instance;
                BridgeAccessibilityService bridge=BridgeAccessibilityService.getInstance();
                if(!optedIn(context)||BridgeState.PAUSED.get()||!selected(context)||bridge==null||!bridge.focusedSearchEditor(window)){
                    result.set(new SearchInputPolicy.Result(false,false,"ime_search_not_ready"));done.countDown();return;
                }
                InputConnection connection=service==null?null:service.getCurrentInputConnection();
                if(service==null||!service.inputStarted||connection==null){ui.postDelayed(this,100);return;}
                long generation=service.epoch;
                SearchInputPolicy.Port port=new SearchInputPolicy.Port(){
                    public boolean valid(){
                        EditorInfo info=service.getCurrentInputEditorInfo();
                        return !cancelled.get()&&!fence.cancelled()&&SystemClock.elapsedRealtime()<deadline&&
                                instance==service&&service.inputStarted&&service.epoch==generation&&service.getCurrentInputConnection()==connection&&
                                optedIn(context)&&selected(context)&&!BridgeState.PAUSED.get()&&info!=null&&
                                SearchInputPolicy.editorAllowed(info.packageName,info.inputType)&&
                                XianyuExecutionEnvironment.capture(service).ready()&&bridge.focusedSearchEditor(window);
                    }
                    public String text(){
                        if(!valid())return null;
                        ExtractedTextRequest request=new ExtractedTextRequest();request.hintMaxChars=201;request.hintMaxLines=2;
                        ExtractedText extracted=connection.getExtractedText(request,0);
                        if(extracted==null||extracted.text==null||extracted.startOffset!=0||extracted.partialStartOffset>=0||extracted.partialEndOffset>=0||extracted.text.length()>200)return null;
                        return extracted.text.toString();
                    }
                    public boolean replace(String old,String desired){
                        if(!valid()||!old.equals(text()))return false;
                        if(!connection.setSelection(0,old.length())||!valid()||!fence.begin(SystemClock.elapsedRealtime(),deadline))return false;
                        wrote.set(true);
                        return connection.commitText(desired,1);
                    }
                };
                result.set(SearchInputPolicy.apply(port,value));done.countDown();
            }catch(RuntimeException unavailable){result.set(new SearchInputPolicy.Result(false,wrote.get(),"ime_search_unavailable"));done.countDown();}
        }};
        if(!ui.post(operation))return outcome(false,false,"ime_search_not_ready");
        try{
            if(!done.await(budget,TimeUnit.MILLISECONDS))return outcome(false,wrote.get()||fence.started(),"ime_search_result_unverified");
        }catch(InterruptedException interrupted){Thread.currentThread().interrupt();return outcome(false,wrote.get()||fence.started(),"ime_search_interrupted");}
        finally{fence.cancel();cancelled.set(true);ui.removeCallbacks(operation);}
        SearchInputPolicy.Result completed=result.get();
        return completed==null?outcome(false,wrote.get(),"ime_search_result_unverified"):outcome(completed.success,wrote.get()||completed.wrote,completed.message);
    }

    static boolean selected(Context context){return componentMatches(context,Settings.Secure.getString(context.getContentResolver(),Settings.Secure.DEFAULT_INPUT_METHOD));}
    static boolean enabled(Context context){
        InputMethodManager manager=context.getSystemService(InputMethodManager.class);
        if(manager==null)return false;
        // Public since API 3. ENABLED_INPUT_METHODS is forbidden to targetSdk >=34 apps.
        for(InputMethodInfo method:manager.getEnabledInputMethodList())
            if(componentMatches(context,method.getId()))return true;
        return false;
    }
    private static boolean componentMatches(Context context,String id){
        ComponentName actual=id==null?null:ComponentName.unflattenFromString(id);
        return new ComponentName(context,OinInputService.class).equals(actual);
    }
    static JSONObject input(String value,long remaining) throws JSONException {
        OinInputService service=instance;
        if(service==null)return outcome(false,false,"oin_input_method_not_active");
        if(Looper.myLooper()==Looper.getMainLooper())return outcome(false,false,"description_requires_task_worker");
        long budget=Math.min(6000L,Math.max(0,remaining));
        if(budget<1000)return outcome(false,false,"description_input_budget_exhausted");
        long deadline=SystemClock.elapsedRealtime()+budget;
        AtomicBoolean cancelled=new AtomicBoolean(false),mayHaveWritten=new AtomicBoolean(false);
        DescriptionInputPolicy.WriteFence fence=new DescriptionInputPolicy.WriteFence();
        AtomicReference<DescriptionInputPolicy.Result> result=new AtomicReference<>();
        CountDownLatch done=new CountDownLatch(1);
        Runnable operation=()->{
            try{
                if(cancelled.get()||SystemClock.elapsedRealtime()>=deadline)return;
                long generation=service.epoch;InputConnection connection=service.getCurrentInputConnection();
                BridgeAccessibilityService bridge=BridgeAccessibilityService.getInstance();
                if(connection==null||bridge==null){result.set(new DescriptionInputPolicy.Result(false,false,"description_input_not_ready"));return;}
                int window=bridge.descriptionInputWindow(false);
                DescriptionInputPolicy.Port port=new DescriptionInputPolicy.Port(){
                    public boolean valid(){
                        EditorInfo info=service.getCurrentInputEditorInfo();
                        return !cancelled.get()&&!fence.cancelled()&&SystemClock.elapsedRealtime()<deadline&&instance==service&&
                            service.inputStarted&&service.epoch==generation&&service.getCurrentInputConnection()==connection&&
                            info!=null&&DescriptionInputPolicy.editorAllowed(info.packageName,info.inputType)&&
                            !BridgeState.PAUSED.get()&&window>=0&&
                            XianyuExecutionEnvironment.capture(service).ready()&&
                            bridge.sameActiveWindow(DescriptionInputPolicy.PACKAGE,window)&&
                            !cancelled.get()&&!fence.cancelled()&&SystemClock.elapsedRealtime()<deadline;
                    }
                    public String text(){
                        if(!valid())return null;
                        ExtractedTextRequest request=new ExtractedTextRequest();request.hintMaxChars=5001;request.hintMaxLines=1000;
                        ExtractedText text=connection.getExtractedText(request,0);
                        if(text==null||text.text==null||text.startOffset!=0||text.partialStartOffset>=0||text.partialEndOffset>=0||text.text.length()>5000)return null;
                        return text.text.toString();
                    }
                    public boolean emptyDescriptionVisible(){return valid()&&bridge.descriptionInputWindow(true)==window;}
                    public boolean commit(String desired){
                        if(!valid()||!fence.begin(SystemClock.elapsedRealtime(),deadline))return false;
                        mayHaveWritten.set(true);
                        return connection.commitText(desired,1);
                    }
                };
                result.set(DescriptionInputPolicy.apply(port,value));
            }catch(RuntimeException unavailable){result.set(new DescriptionInputPolicy.Result(false,mayHaveWritten.get(),"description_input_unavailable"));}
            finally{done.countDown();}
        };
        if(!service.main.post(operation))return outcome(false,false,"description_input_not_ready");
        try{
            if(!done.await(budget,TimeUnit.MILLISECONDS)){
                fence.cancel();cancelled.set(true);service.main.removeCallbacks(operation);
                return outcome(false,mayHaveWritten.get()||fence.started(),"description_input_result_unverified");
            }
        }catch(InterruptedException interrupted){fence.cancel();cancelled.set(true);service.main.removeCallbacks(operation);Thread.currentThread().interrupt();return outcome(false,mayHaveWritten.get()||fence.started(),"description_input_interrupted");}
        DescriptionInputPolicy.Result completed=result.get();
        return completed==null?outcome(false,mayHaveWritten.get(),"description_input_budget_exhausted"):
                outcome(completed.success,completed.wrote,completed.message);
    }
    private static JSONObject outcome(boolean success,boolean wrote,String message) throws JSONException {
        return new JSONObject().put("success",success).put("verified",success).put("may_have_written",wrote).put("message",message);
    }
}
