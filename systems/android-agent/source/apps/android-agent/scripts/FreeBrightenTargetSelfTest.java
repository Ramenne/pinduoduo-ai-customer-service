package com.feifei.phonebridge;
public final class FreeBrightenTargetSelfTest {
 static void check(boolean v){if(!v)throw new AssertionError();}
 public static void main(String[] args){
  String label="今日数据\n0\n宝贝曝光\n0\n宝贝浏览\n加曝光\n一键擦亮\n+5曝光";
  int[] box={16,153,444,489};float[] p=FreeBrightenTarget.locate(label,box,true,720,1600);
  check(p!=null&&p[0]>48&&p[0]<228&&p[1]>387&&p[1]<458);
  check(FreeBrightenTarget.locate(label.replace("一键擦亮","试试超强擦亮"),box,true,720,1600)==null);
  check(FreeBrightenTarget.locate(label,box,false,720,1600)==null);
  check(FreeBrightenTarget.locate(label,new int[]{-1,153,444,489},true,720,1600)==null);
  check(FreeBrightenTarget.locate(label,new int[]{16,153,700,489},true,720,1600)==null);
  check(FreeBrightenTarget.locate(label,new int[]{16,153,444,200},true,720,1600)==null);
  System.out.println("Free brighten target checks passed: measured bounds, page identity, paid offer, invalid geometry");
 }
}
