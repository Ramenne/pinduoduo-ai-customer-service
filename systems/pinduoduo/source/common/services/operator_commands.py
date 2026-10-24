"""仅识别当前工作人员明确授权发送的短指令；疑问、否定、条件句均不执行。"""
import re


def explicitly_send(text):
    if not isinstance(text,str) or len(text)>80:
        return False
    value=re.sub(r'[\s，,。.!！、]','',text).lower()
    return bool(re.fullmatch(
        r'(?:(?:ok|好的|好|行|可以|确认|嗯|那就|那))*(?:(?:你|请|帮我|麻烦你))?'
        r'(?:(?:按这个|按刚才的|按补充的))?(?:发送给|发给)(?:顾客|客户|买家|他|她)(?:吧|就行|即可|了)?'
        r'|确认发送',value))


def stop_sending(text):
    """当前人工原文的保守否决。只停止本轮，不伪造已送达或关闭所有问题。"""
    if not isinstance(text, str):
        return False
    return bool(re.search(r'不用再?发|不要再?发|先别发|别再发|无需回复|不用回复|不需要回复|已经回(?:复|过)|已回复|已经处理|已经发送|已发送|已经发过|已经发了|已发过|发过去了', text))


def current_send(text):
    if stop_sending(text) or not isinstance(text, str) or len(text) > 4000:
        return False
    if explicitly_send(text):
        return True
    # 允许“有货，按这个回复发给顾客吧”等混合输入；疑问、条件、引语不授权。
    if re.search(r'[?？“”"「」]|如果|假如|能否|可以吗|能不能|客户说|顾客说|例如|比如', text):
        return False
    tail = re.split(r'[，,。；;\n]', text.strip())[-1]
    return explicitly_send(tail)


def acknowledgement(text):
    if not isinstance(text, str):
        return False
    value = re.sub(r'[\s，,。.!！、]', '', text).lower()
    return value in {'ok','okay','好的','好','行','嗯','收到','明白','知道了','可以','可以的'}


def reported_sent(text):
    """仅识别不含新事实的明确已回复说明，不把未来发送要求当作已发送。"""
    if not isinstance(text, str):
        return False
    value = re.sub(r'[\s，,。.!！、]', '', text).lower()
    return bool(re.fullmatch(r'(?:(?:可以发送|可以发|好的|ok)(?:并且|而且|并)?)*'
        r'(?:我|我们)?(?:已经|已)(?:发送|发过|回复|回过)(?:了)?', value))
