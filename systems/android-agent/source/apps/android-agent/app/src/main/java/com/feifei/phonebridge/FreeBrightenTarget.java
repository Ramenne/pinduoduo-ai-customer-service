package com.feifei.phonebridge;

/** Coordinates are derived only inside the measured free-action panel, never caller supplied. */
final class FreeBrightenTarget {
 static float[] locate(String description,int[] b,boolean selling,int width,int height){
  if(!selling||description==null||!description.matches("今日数据\\n[0-9]+\\n宝贝曝光\\n[0-9]+\\n宝贝浏览\\n加曝光\\n一键擦亮\\n\\+[0-9]+曝光")||b==null||b.length!=4)return null;
  int w=b[2]-b[0],h=b[3]-b[1];
  if(w<=0||h<=0||b[0]<0||b[1]<0||b[2]>width||b[3]>height||w<width*.45f||w>width*.8f||h<height*.12f||h>height*.3f||w/(float)h<1||w/(float)h>1.6f)return null;
  return new float[]{b[0]+w*.28f,b[1]+h*.80f};
 }
}
