package com.feifei.phonebridge;
final class SearchInputPolicySelfTest {
    static int checks;
    static void check(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}
    static final class Editor implements SearchInputPolicy.Port {
        String body="旧搜索词";boolean ready=true,partial,changeAfterRead,changeAfterWrite,wrongReadback;int writes;
        public boolean valid(){return ready;}
        public String text(){String actual=partial?null:body;if(changeAfterRead)ready=false;return actual;}
        public boolean replace(String old,String desired){check(old.equals(body),"replacement uses read value");writes++;body=wrongReadback?"不完整":desired;if(changeAfterWrite)ready=false;return true;}
    }
    public static void main(String[] args){
        check(SearchInputPolicy.editorAllowed("com.taobao.idlefish",1),"Xianyu text editor");
        check(!SearchInputPolicy.editorAllowed("other.app",1),"other package rejected");
        for(int variant:new int[]{0x80,0x90,0xe0})check(!SearchInputPolicy.editorAllowed("com.taobao.idlefish",1|variant),"password rejected");
        check(!SearchInputPolicy.editorAllowed("com.taobao.idlefish",2),"number field rejected");
        Editor ok=new Editor();check(SearchInputPolicy.apply(ok,"桌面收纳").success&&ok.body.equals("桌面收纳")&&ok.writes==1,"replace and verify Chinese");
        check(SearchInputPolicy.apply(ok,"桌面收纳").success&&ok.writes==1,"same result never retypes");
        Editor empty=new Editor();empty.body="";check(SearchInputPolicy.apply(empty,"便签纸").success,"empty search accepted");
        for(String bad:new String[]{"","a".repeat(81),"a\nb","a\tb"}){Editor e=new Editor();check(!SearchInputPolicy.apply(e,bad).success&&e.writes==0,"invalid request never writes");}
        Editor partial=new Editor();partial.partial=true;check(!SearchInputPolicy.apply(partial,"台灯").success&&partial.writes==0,"partial read never writes");
        Editor longText=new Editor();longText.body="a".repeat(201);check(!SearchInputPolicy.apply(longText,"台灯").success&&longText.writes==0,"bounded existing text");
        Editor cancelled=new Editor();cancelled.changeAfterRead=true;check(!SearchInputPolicy.apply(cancelled,"台灯").success&&cancelled.writes==0,"cancel before write");
        Editor changed=new Editor();changed.changeAfterWrite=true;SearchInputPolicy.Result late=SearchInputPolicy.apply(changed,"台灯");check(!late.success&&late.wrote&&changed.writes==1,"focus change after write stays unknown");
        Editor mismatch=new Editor();mismatch.wrongReadback=true;SearchInputPolicy.Result failed=SearchInputPolicy.apply(mismatch,"台灯");check(!failed.success&&failed.wrote&&mismatch.writes==1,"mismatch stays unknown");
        Editor disabled=new Editor();disabled.ready=false;check(!SearchInputPolicy.apply(disabled,"台灯").success&&disabled.writes==0,"disabled never writes");
        System.out.println("Search input policy: "+checks+" checks passed");
    }
}
