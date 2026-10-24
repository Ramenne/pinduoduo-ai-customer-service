package com.feifei.phonebridge;

final class DescriptionInputPolicySelfTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    static final class Editor implements DescriptionInputPolicy.Port {
        String body="";boolean ready=true,anchor=true,partial=false,changeAfterRead=false,changeAfterCommit=false,wrongReadback=false;
        int writes;
        public boolean valid(){return ready;}
        public String text(){String value=partial?null:body;if(changeAfterRead)ready=false;return value;}
        public boolean emptyDescriptionVisible(){return anchor;}
        public boolean commit(String value){writes++;body=wrongReadback?"truncated":value;if(changeAfterCommit)ready=false;return true;}
    }
    public static void main(String[] args){
        int multiline=1|0x20000;
        check(DescriptionInputPolicy.editorAllowed("com.taobao.idlefish",multiline),"Xianyu multiline");
        check(!DescriptionInputPolicy.editorAllowed("another.app",multiline),"foreign editor");
        for(int variant:new int[]{0x80,0x90,0xe0})check(!DescriptionInputPolicy.editorAllowed("com.taobao.idlefish",multiline|variant),"password variation");
        check(!DescriptionInputPolicy.editorAllowed("com.taobao.idlefish",2|0x20000),"numeric editor");
        check(!DescriptionInputPolicy.editorAllowed("com.taobao.idlefish",1),"single line editor");
        Editor editor=new Editor();String desired="  Ａ商品\n第二行  ";
        check(DescriptionInputPolicy.apply(editor,desired).success&&desired.equals(editor.body)&&editor.writes==1,"exact multiline write and readback");
        check(DescriptionInputPolicy.apply(editor,desired).success&&editor.writes==1,"same text only readback");
        check(!DescriptionInputPolicy.apply(editor,"different").success&&editor.writes==1&&desired.equals(editor.body),"never overwrite existing description");
        Editor missing=new Editor();missing.anchor=false;check(!DescriptionInputPolicy.apply(missing,"x").success&&missing.writes==0,"missing description page");
        Editor partial=new Editor();partial.partial=true;check(!DescriptionInputPolicy.apply(partial,"x").success&&partial.writes==0,"partial extraction");
        Editor cancelled=new Editor();cancelled.changeAfterRead=true;check(!DescriptionInputPolicy.apply(cancelled,"x").success&&cancelled.writes==0,"cancel before write");
        Editor switched=new Editor();switched.changeAfterCommit=true;DescriptionInputPolicy.Result late=DescriptionInputPolicy.apply(switched,"x");check(!late.success&&late.wrote&&switched.writes==1,"changed editor stays unverified");
        Editor mismatch=new Editor();mismatch.wrongReadback=true;check(!DescriptionInputPolicy.apply(mismatch,"x").success&&mismatch.writes==1,"readback mismatch");
        Editor large=new Editor();large.body="a".repeat(5001);check(!DescriptionInputPolicy.apply(large,"x").success&&large.writes==0,"bounded existing text");
        check(!DescriptionInputPolicy.apply(new Editor(),"").success,"empty request");
        check(!DescriptionInputPolicy.apply(new Editor(),"a".repeat(5001)).success,"bounded requested text");
        String boundary="a".repeat(5000);Editor limit=new Editor();check(DescriptionInputPolicy.apply(limit,boundary).success,"maximum requested length");
        java.util.Set<String> fields=new java.util.HashSet<>(java.util.Arrays.asList("action","package","value"));
        check(CloudTaskParametersContract.validate("action",fields,"set_description_text","com.taobao.idlefish",false,null,null,null,null,desired,null,null,null,null)==null,"exact cloud description contract");
        fields.add("selector");check(CloudTaskParametersContract.validate("action",fields,"set_description_text","com.taobao.idlefish",false,null,null,null,null,desired,null,null,null,null)!=null,"no arbitrary target selector");
        DescriptionInputPolicy.WriteFence stopped=new DescriptionInputPolicy.WriteFence();
        check(stopped.cancel()&&!stopped.begin(1,10)&&!stopped.started(),"cancelled request cannot begin a write");
        DescriptionInputPolicy.WriteFence started=new DescriptionInputPolicy.WriteFence();
        check(started.begin(1,10)&&!started.cancel()&&started.started(),"in-flight write remains uncertain on cancellation");
        check(!started.begin(2,10),"one write permit only");
        DescriptionInputPolicy.WriteFence expired=new DescriptionInputPolicy.WriteFence();
        check(!expired.begin(10,10)&&!expired.started(),"deadline cannot begin a write");
        System.out.println("DescriptionInputPolicySelfTest: "+checks+" checks passed (policy only; physical IME pending)");
    }
}
