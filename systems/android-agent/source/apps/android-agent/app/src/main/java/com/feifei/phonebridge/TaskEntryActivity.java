package com.feifei.phonebridge;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
/** Bound users land on task controls; connection setup remains directly accessible. */
public final class TaskEntryActivity extends Activity {
 @Override public void onCreate(Bundle saved){super.onCreate(saved);boolean bound=!CloudState.deviceId(this).isEmpty()&&!CloudState.isRevoked(this);startActivity(new Intent(this,bound?NativeTaskActivity.class:MainActivity.class));finish();}
}
