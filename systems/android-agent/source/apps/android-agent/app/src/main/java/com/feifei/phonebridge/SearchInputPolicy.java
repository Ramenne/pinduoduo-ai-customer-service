package com.feifei.phonebridge;

/** Optional IME replacement only in a positively identified Xianyu search editor. */
final class SearchInputPolicy {
    static boolean editorAllowed(String pkg,int type){
        int variation=type&0xff0;
        return "com.taobao.idlefish".equals(pkg)&&(type&0xf)==1&&
                variation!=0x80&&variation!=0x90&&variation!=0xe0;
    }
    interface Port { boolean valid(); String text(); boolean replace(String old,String desired); }
    static final class Result {
        final boolean success,wrote;final String message;
        Result(boolean success,boolean wrote,String message){this.success=success;this.wrote=wrote;this.message=message;}
    }
    static Result apply(Port port,String desired){
        if(desired==null||desired.isEmpty()||desired.length()>80||desired.codePoints().anyMatch(Character::isISOControl))return new Result(false,false,"ime_invalid_search_text");
        if(!port.valid())return new Result(false,false,"ime_search_not_ready");
        String old=port.text();
        if(old==null||old.length()>200)return new Result(false,false,"ime_readback_unavailable");
        if(!port.valid())return new Result(false,false,"ime_search_changed");
        if(desired.equals(old))return new Result(true,false,"ime_search_readback_verified");
        if(!port.replace(old,desired))return new Result(false,true,"ime_search_result_unverified");
        if(!port.valid())return new Result(false,true,"ime_search_result_unverified");
        String actual=port.text();
        return port.valid()&&desired.equals(actual)?new Result(true,true,"ime_search_readback_verified"):
                new Result(false,true,"ime_search_result_unverified");
    }
}
