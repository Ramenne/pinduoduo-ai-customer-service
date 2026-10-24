package com.feifei.phonebridge;

/** Only an empty Xianyu multiline description, or a readback of this exact value. */
final class DescriptionInputPolicy {
    static final String PACKAGE="com.taobao.idlefish";
    static final String PLACEHOLDER="描述, 描述一下宝贝的品牌型号、货品来源…";
    private DescriptionInputPolicy() {}
    static final class WriteFence {
        private final java.util.concurrent.atomic.AtomicInteger phase=new java.util.concurrent.atomic.AtomicInteger();
        boolean begin(long now,long deadline){return now<deadline&&phase.compareAndSet(0,1);}
        boolean cancel(){return phase.compareAndSet(0,2);}
        boolean started(){return phase.get()==1;}
        boolean cancelled(){return phase.get()==2;}
    }
    static boolean editorAllowed(String pkg,int type) {
        int variation=type&0xff0;
        return PACKAGE.equals(pkg)&&(type&0xf)==1&&(type&0x20000)!=0&&
                variation!=0x80&&variation!=0x90&&variation!=0xe0;
    }
    interface Port {
        boolean valid();
        String text(); // null means partial, unavailable or over the bound.
        boolean emptyDescriptionVisible();
        boolean commit(String value);
    }
    static final class Result {
        final boolean success,wrote;
        final String message;
        Result(boolean success,boolean wrote,String message){this.success=success;this.wrote=wrote;this.message=message;}
    }
    static Result apply(Port port,String value) {
        if(BoundedXianyuActions.validateText(value)!=null)return result(false,false,"invalid_description_text");
        if(!port.valid())return result(false,false,"description_input_not_ready");
        String old=port.text();
        if(old==null||old.length()>BoundedXianyuActions.MAX_TEXT_CHARS)return result(false,false,"description_readback_unavailable");
        if(!port.valid())return result(false,false,"description_input_changed");
        if(value.equals(old))return result(true,false,"description_text_readback_verified");
        if(!old.isEmpty())return result(false,false,"description_editor_not_empty");
        if(!port.emptyDescriptionVisible()||!port.valid())return result(false,false,"description_page_not_confirmed");
        // commitText only into the verified empty editor. Never replace unrelated existing text.
        if(!port.commit(value))return result(false,true,"description_commit_not_confirmed");
        if(!port.valid())return result(false,true,"description_input_result_unverified");
        String actual=port.text();
        return port.valid()&&value.equals(actual)?result(true,true,"description_text_readback_verified"):
                result(false,true,"description_input_result_unverified");
    }
    private static Result result(boolean success,boolean wrote,String message){return new Result(success,wrote,message);}
}
