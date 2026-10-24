package com.feifei.phonebridge;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.view.View;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

/** One owner of system-bar/cutout/IME space. Uses original padding, never accumulates insets. */
final class MobileUi {
    private MobileUi() {}
    static int dp(Activity activity,int value){return Math.round(value*activity.getResources().getDisplayMetrics().density);}
    static void safeArea(Activity activity,View root){
        WindowCompat.setDecorFitsSystemWindows(activity.getWindow(),false);
        activity.getWindow().setStatusBarColor(Color.TRANSPARENT);
        activity.getWindow().setNavigationBarColor(Color.TRANSPARENT);
        WindowInsetsControllerCompat controller=WindowCompat.getInsetsController(activity.getWindow(),root);
        controller.setAppearanceLightStatusBars(true);controller.setAppearanceLightNavigationBars(true);
        int l=root.getPaddingLeft(),t=root.getPaddingTop(),r=root.getPaddingRight(),b=root.getPaddingBottom();
        ViewCompat.setOnApplyWindowInsetsListener(root,(view,insets)->{
            Insets bars=insets.getInsets(WindowInsetsCompat.Type.systemBars()|WindowInsetsCompat.Type.displayCutout());
            Insets keyboard=insets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(l+bars.left,t+bars.top,r+bars.right,b+Math.max(bars.bottom,keyboard.bottom));
            return WindowInsetsCompat.CONSUMED;
        });
        ViewCompat.requestApplyInsets(root);
    }
    static GradientDrawable surface(int color,int radius){GradientDrawable background=new GradientDrawable();background.setColor(color);background.setCornerRadius(radius);return background;}
}
