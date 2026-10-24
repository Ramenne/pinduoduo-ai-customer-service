"""Seller semantics measured on Xianyu 7.25.40, including Flutter groups."""
import re


def visible_texts(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('package') != 'com.taobao.idlefish' or snapshot.get('truncated') or not isinstance(snapshot.get('nodes'), list):
        return []
    return [str(n.get('description') or n.get('text') or '') for n in snapshot['nodes']
            if isinstance(n, dict) and n.get('visible_to_user') is True and not n.get('password')]


def selling_tab(value):
    return bool(re.fullmatch(r'(?:\d+\s*\n\s*)?在卖', value.strip()))


def confirmed_selling(snapshot):
    texts = visible_texts(snapshot)
    return (sum(selling_tab(t) for t in texts) == 1 and
            sum(t == '草稿' for t in texts) == 1 and
            sum(t == '已下架' for t in texts) == 1)


def already_brightened(snapshot):
    if not confirmed_selling(snapshot):
        return False
    panels = [t.splitlines() for t in visible_texts(snapshot) if t.splitlines() and t.splitlines()[0] == '今日数据']
    return len(panels) == 1 and ('今日已擦亮' in panels[0] or '试试超强擦亮' in panels[0] and '一键擦亮' not in panels[0])


def brighten_status(snapshot):
    texts=visible_texts(snapshot)
    my_page=sum(t=='我发布的' for t in texts)==1 and sum(t=='我的' for t in texts)==1
    if not (my_page or confirmed_selling(snapshot)):return {'state':'wrong_page'}
    if already_brightened(snapshot) or my_page and any(re.fullmatch(r'明日擦亮(?:\s+曝光\+\d+)?',t) for t in texts):
        return {'state':'already_done'}
    panels=[t for t in texts if re.fullmatch(r'今日数据\n\d+\n宝贝曝光\n\d+\n宝贝浏览\n加曝光\n一键擦亮\n\+\d+曝光',t)]
    if confirmed_selling(snapshot) and len(panels)==1:
        return {'state':'available','parameters':{'action':'tap_free_brighten','package':'com.taobao.idlefish'}}
    candidates=[]
    for kind in ['text','description']:
        for index,n in enumerate(snapshot['nodes']):
            value=n.get(kind,'')
            if (n.get('visible_to_user') is True and n.get('enabled') is True and not n.get('password')
                    and not n.get('editable') and re.fullmatch(r'(?:一键擦亮|今日擦亮)(?:\s+曝光\+\d+)?',value)
                    and sum(other.get('visible_to_user') is True and other.get(kind)==value for other in snapshot['nodes'])==1):
                candidates.append((index,{'action':'click_unique_node' if n.get('clickable') else 'tap_unique_node',
                    'package':'com.taobao.idlefish','selector':{'kind':kind,'value':value}}))
    # A single node may expose the same string in text and description.
    if candidates and len({index for index,c in candidates})==1:
        return {'state':'entry' if my_page and candidates[0][1]['selector']['value'].startswith('今日擦亮') else 'available','parameters':candidates[0][1]}
    # This banner becomes a publishing promotion after the ordinary action.
    # Navigate to the known selling list to read authoritative status.
    if my_page and not any(t.startswith(('一键擦亮','今日擦亮','试试超强擦亮')) for t in texts):
        found=[n for n in snapshot['nodes'] if n.get('visible_to_user') is True and n.get('enabled') is True and n.get('text')=='我发布的']
        if len(found)==1:
            return {'state':'entry','parameters':{'action':'tap_unique_node','package':'com.taobao.idlefish','selector':{'kind':'text','value':'我发布的'}}}
    return {'state':'unavailable'}


def selling_count(snapshot):
    if not confirmed_selling(snapshot):return None
    for t in visible_texts(snapshot):
        found=re.fullmatch(r'(\d+)\s*\n\s*在卖',t.strip())
        if found:return int(found[1])
    if any('你还没有在卖的宝贝' in t for t in visible_texts(snapshot)):return 0
    return None
