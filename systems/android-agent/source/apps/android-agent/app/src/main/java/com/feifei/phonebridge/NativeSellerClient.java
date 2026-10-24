package com.feifei.phonebridge;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import okhttp3.*;

/** Device proof never leaves Keystore. Persists only a pending job's non-secret parameters. */
final class NativeSellerClient {
    private final Context context;
    private final SharedPreferences prefs;
    private final OkHttpClient http=new OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
            .connectTimeout(10,TimeUnit.SECONDS).readTimeout(20,TimeUnit.SECONDS).callTimeout(25,TimeUnit.SECONDS).build();
    private volatile boolean closed;
    private volatile Call active;
    NativeSellerClient(Context context){this.context=context.getApplicationContext();prefs=context.getSharedPreferences("oin_native_jobs",Context.MODE_PRIVATE);}
    boolean pending(){return prefs.contains("pending:"+CloudState.deviceId(context));}
    JSONObject cancel(String job) throws Exception { return request("cancel",new JSONObject().put("job_id",job).toString()); }
    JSONObject inspect(String job) throws Exception {return request("inspect",new JSONObject().put("job_id",job).toString());}
    JSONObject control(String job,String action) throws Exception {return control(job,action,false);}
    JSONObject control(String job,String action,boolean confirm) throws Exception {JSONObject body=new JSONObject().put("job_id",job).put("action",action);if(confirm)body.put("confirm_manual",true);return request("control",body.toString());}
    JSONObject metrics() throws Exception{return request("metrics","{}");}
    JSONObject inventory() throws Exception{return request("inventory","{}");}
    JSONObject start(JSONObject selected) throws Exception {
        String slot="pending:"+CloudState.deviceId(context);
        String payload=prefs.getString(slot,null);
        if(payload==null){selected.put("idempotency_key",UUID.randomUUID().toString());payload=selected.toString();
            if(!prefs.edit().putString(slot,payload).commit())throw new IOException("task_pending_store_failed");}
        try{
            JSONObject result=request("start",payload);
            if(!prefs.edit().remove(slot).commit())throw new IOException("task_accepted_pending_retained");
            return result;
        }catch(Rejected rejected){
            // Only explicit 4xx response proves this request was not newly accepted.
            if(rejected.status!=409)prefs.edit().remove(slot).commit();
            throw rejected;
        }
    }
    private JSONObject request(String command,String payload) throws Exception {
        String device=CloudState.deviceId(context);
        if(device.isEmpty()||CloudState.isRevoked(context))throw new IOException("device_not_bound");
        long stamp=System.currentTimeMillis()/1000;String nonce=UUID.randomUUID().toString();
        String signature=DeviceIdentity.sign(context,NativeSellerProtocol.message(device,command,stamp,nonce,payload));
        JSONObject envelope=new JSONObject().put("command",command).put("issued_at",stamp)
                .put("nonce",nonce).put("payload_text",payload).put("signature",signature);
        Request request=new Request.Builder().url(CloudConfig.endpoint("api/v1/devices/"+device+"/seller-native"))
                .post(RequestBody.create(envelope.toString(),MediaType.get("application/json; charset=utf-8"))).build();
        Call call=http.newCall(request);active=call;
        if(closed||Thread.currentThread().isInterrupted()){call.cancel();throw new IOException("task_request_cancelled");}
        try(Response response=call.execute()){
            if(response.body()==null)throw new IOException("task_response_empty");
            ByteArrayOutputStream data=new ByteArrayOutputStream();
            try(InputStream input=response.body().byteStream()){
                byte[] buffer=new byte[8192];int count;
                while((count=input.read(buffer))!=-1){
                    if(closed||Thread.currentThread().isInterrupted())throw new IOException("task_request_cancelled");
                    if(data.size()+count>1048576)throw new IOException("task_response_too_large");data.write(buffer,0,count);
                }
            }
            JSONObject result=new JSONObject(data.toString(StandardCharsets.UTF_8.name()));
            if(!response.isSuccessful()){
                String error=result.optString("error","task_request_failed");
                if(response.code()>=400&&response.code()<500)throw new Rejected(response.code(),error);
                throw new IOException(error);
            }
            return result;
        }finally{active=null;}
    }
    void close(){closed=true;Call call=active;if(call!=null)call.cancel();}
    static final class Rejected extends IOException{
        final int status;Rejected(int status,String error){super(error);this.status=status;}
    }
}
