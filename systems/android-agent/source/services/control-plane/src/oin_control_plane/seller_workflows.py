"""Built-in paths measured on the connected Xianyu phone; no guessed publishing steps."""

PACKAGE = 'com.taobao.idlefish'

# Shared product catalogue for web and native; task kinds and execution contracts stay stable.
SECTIONS = [
    {'id':'publish','name':'上新','summary':'上新调研与商品资料整理，不启动手机执行任务',
     'progress':'在这里整理研究、标题、描述、图片和价格，交给AI共同完善后由你决定如何上新。'},
    {'id':'preheat','name':'新号预热','summary':'每日签到、正常浏览和停留，不要求品类关键词',
     'progress':'每日任务按调研运行；签到画布仍需本人完成，真实交易付款由本人操作。'},
    {'id':'maintain','name':'日常维护','summary':'卖家每日擦亮与看数、提醒优化和上新；买家每日签到与浏览',
     'progress':'当前账号无在售商品；记录真实空态并提醒上新。有商品的自动擦亮待实页接通。'},
]


def recipe_section(kind):
    return 'publish' if kind=='publish' else 'preheat' if kind=='research' else 'maintain'



def observe(*descriptions):
    return {'task_type':'observe','parameters':{},'effect':'read',
            'expect':[{'kind':'description','value':v} for v in descriptions]}


def click(description):
    return {'task_type':'action','effect':'navigation',
            'parameters':{'action':'click_unique_node','package':PACKAGE,
                          'selector':{'kind':'description','value':description}}}


def tap_text(value):
    return {'task_type':'action','effect':'navigation',
            'parameters':{'action':'tap_unique_node','package':PACKAGE,
                          'selector':{'kind':'text','value':value}}}


BUILTINS = {
    'open_publish_form_v1': {
        'section':'publish',
        'name':'上新准备 · 打开发布表单',
        'kind':'research',  # Existing read/navigation-only ledger kind; never marks an item published.
        'boundary':'从闲鱼首页运行；只打开表单，不填充、不上传、不发布。',
        'steps':[
            {'task_type':'action','effect':'navigation','parameters':{'action':'launch_app','package':PACKAGE}},
            observe('卖闲置'),
            click('卖闲置'),
            observe('发闲置\n自己拍图卖·啥都能换钱'),
            click('发闲置\n自己拍图卖·啥都能换钱'),
            observe('发布, 发布','添加图片'),
        ],
    },
    'open_selling_overview_v1': {
        'section':'maintain',
        'name':'维护准备 · 打开我发布的',
        'kind':'research',
        'min_agent':36,
        'boundary':'从闲鱼底部可见“我的”的页面运行；只读取在卖、草稿、已下架入口，不编辑或擦亮商品。',
        'steps':[
            {'task_type':'action','effect':'navigation','parameters':{'action':'launch_app','package':PACKAGE}},
            {'task_type':'observe','parameters':{},'effect':'read',
             'expect':[{'kind':'text','value':'我的'}]},
            tap_text('我的'),
            {'task_type':'observe','parameters':{},'effect':'read',
             'expect':[{'kind':'text','value':'我发布的'}]},
            tap_text('我发布的'),
            observe('在卖','草稿','已下架'),
        ],
    },
}

# Current consumer: chosen saved item's description -> scoped IME -> exact InputConnection readback.
# Still a draft preparation workflow, never a publish or edit of an existing listing.
BUILTINS['prepare_description_v1']={
    'section':'publish',
    'name':'上新准备 · 打开表单并填写描述',
    'kind':'research','min_agent':41,'requires_item':True,
    'boundary':'先选真实商品素材；手机需更新并选择 OIN 闲鱼填写。从可见“卖闲置”的页面开始，只向空白描述填写，不选图、不填写价格、不发布。',
    'steps':BUILTINS['open_publish_form_v1']['steps']+[
        click('描述, 描述一下宝贝的品牌型号、货品来源…'),
        observe('发布, 发布','完成'),
        {'task_type':'action','effect':'navigation',
         'parameters':{'action':'set_description_text','package':PACKAGE,'value':'{{description}}'}},
        observe('发布, 发布','完成'),
        click('完成'),
        observe('发布, 发布','添加图片'),
    ],
}


# Everyday tasks follow the supplied work arrangement. Keywords are not prerequisites.
# Canvas sign-in and a populated selling list still require a human checkpoint until
# their actual action/confirmation nodes are measured; never call that automated.
def named(step, name):
    return {**step, 'name':name}

def text_page(*values):
    return {'task_type':'observe','parameters':{},'effect':'read','expect':[{'kind':'text','value':v} for v in values]}

def wait(seconds, name):
    return {'task_type':'wait','parameters':{'seconds':seconds},'effect':'read','name':name}

def route():
    return {'task_type':'route_home','parameters':{},'effect':'navigation','name':'回到闲鱼首页，核对推荐和底部导航'}

START_DAILY=[named({'task_type':'action','effect':'navigation','parameters':{'action':'launch_app','package':PACKAGE}},'打开闲鱼'),
    named({'task_type':'observe','parameters':{},'effect':'read','expect':[{'kind':'resource_id','value':'com.taobao.idlefish:id/fish_layer_container_id'}]},'读取当前闲鱼页面'),route()]
SIGNIN=[named(tap_text('我的'),'进入我的'),named(text_page('现金签到'),'核对每日签到入口'),
    named(tap_text('现金签到'),'打开当前现金签到页面'),named(text_page(),'保存签到页面实际状态'),
    {'task_type':'manual','parameters':{},'effect':'navigation','name':'完成签到/领取后确认继续（当前页面为游戏画布，尚不能自动验证签到）'},route()]
BROWSE=[]
for i in range(5):
    BROWSE += [named(text_page('推荐','我的'),f'读取第{i+1}轮首页推荐'),
        {'task_type':'browse_item','parameters':{},'effect':'navigation','name':f'打开第{i+1}件当前推荐商品，阅读详情'},
        named(text_page(),f'保存第{i+1}件商品详情'),wait(120,f'阅读第{i+1}件商品及页面，停留2分钟'),route(),
        named({'task_type':'action','effect':'navigation','parameters':{'action':'scroll_page','package':PACKAGE,'direction':'down'}},f'浏览下一页推荐（第{i+1}轮）')]
# A final read makes scrolling outcome visible and durable.
BROWSE += [named(text_page('推荐','我的'),'保存本次浏览结束页面')]
BUILTINS['daily_preheat_v1']={'section':'preheat','name':'新号预热 · 每日签到与10分钟浏览','kind':'research','min_agent':47,
    'boundary':'无需品类或关键词。读取签到状态；当前签到画布需本人完成后确认继续。随后浏览5件推荐商品，每件停留2分钟，逐步记录；不发送消息、不下单、不自动收藏。',
    'steps':START_DAILY+SIGNIN+BROWSE}
SELLER_DAILY=[named(tap_text('我的'),'进入卖家个人页'),named(text_page('我发布的'),'核对我的交易入口'),named(tap_text('我发布的'),'读取在售商品'),named(observe('在卖','草稿','已下架'),'保存今日商品和曝光状态'),
    {'task_type':'manual','parameters':{},'effect':'navigation','unless':{'kind':'description','value':'你还没有在卖的宝贝，\n去发布'},'name':'完成今日擦亮并确认继续（有商品时的擦亮节点仍需实页接通）'},
    {'task_type':'report','parameters':{},'effect':'read','name':'整理卖家数据，提醒人工优化与上新'},route()]
BUILTINS['daily_maintenance_v1']={'section':'maintain','name':'日常维护 · 卖家维护与买家日常','kind':'metrics','min_agent':47,
    'boundary':'读取在售与曝光，当前无在售商品则明确略过擦亮并提醒上新；有商品的擦亮目前须本人处理。随后执行每日签到和10分钟买家浏览。所有记录可回看，任务可暂停、继续、结束。',
    'steps':START_DAILY+SELLER_DAILY+SIGNIN+BROWSE}


# v2 supersedes cash/game entry. Existing v1 jobs retain their immutable snapshots.
def phased(steps, phase):
    return [{**step,'phase':phase} for step in steps]

PLAIN_BROWSE=[]
for i in range(5):
    PLAIN_BROWSE += [named(text_page('推荐','我的'),f'确认第{i+1}段浏览页面'),
        wait(120,f'浏览首页推荐 · 第{i+1}段（2分钟）'),
        named({'task_type':'action','effect':'navigation','parameters':{'action':'scroll_page','package':PACKAGE,'direction':'down'}},f'浏览下一页 · 第{i+1}次'),
        named(text_page('推荐','我的'),f'保存第{i+1}段浏览记录')]
FINISH_DAILY=[{'task_type':'report','parameters':{},'effect':'read','name':'整理本次浏览记录与后续提醒','phase':'结果与提醒'}]
BUILTINS['daily_preheat_v2']={'section':'preheat','name':'新号预热 · 日常浏览','kind':'research','min_agent':48,
    'boundary':'自动进入首页，正常浏览10分钟并翻页、保存结果。可确认的广告关闭/跳过后重新核对页面；不进入游戏、现金任务或随机“做任务”页面。签到只在有稳定直接入口时继续适配，不把浏览算成签到。',
    'steps':phased(START_DAILY,'进入闲鱼')+phased(PLAIN_BROWSE,'日常浏览')+FINISH_DAILY}
BUILTINS['daily_maintenance_v2']={'section':'maintain','name':'日常维护 · 商品与浏览','kind':'metrics','min_agent':48,
    'boundary':'先读取在售与曝光并生成提醒，再正常浏览10分钟。当前无在售商品则无需擦亮，提醒准备上新；有商品的自动擦亮仍待实页验证，须本人处理。不进入现金任务和游戏，不自动更改商品。',
    'steps':phased(START_DAILY,'进入闲鱼')+phased(SELLER_DAILY[:-1],'商品维护')+phased([route()],'回到首页')+phased(PLAIN_BROWSE,'日常浏览')+FINISH_DAILY}
SECTIONS[1]['summary']='正常浏览、翻页与停留，保留每天的实际记录'
SECTIONS[1]['progress']='不进入游戏和“做任务”页面；稳定直接签到入口尚未接通。'
SECTIONS[2]['summary']='查看商品与曝光、提醒人工优化和上新，再完成日常浏览'


# Candidate v3: move during the 10-minute browse, preserve interruptions.
def moving_plan(steps):
    return [{**step,'parameters':{**step['parameters'],'browse_motion':True}} if step['task_type']=='wait' else {**step} for step in steps]
for old,new in [('daily_preheat_v2','daily_preheat_v3'),('daily_maintenance_v2','daily_maintenance_v3')]:
    BUILTINS[new]={**BUILTINS[old],'min_agent':50,'steps':moving_plan(BUILTINS[old]['steps'])}

BUILTINS['daily_preheat_v3']['name']='新号预热'
BUILTINS['daily_maintenance_v3']['name']='日常维护'
SECTIONS[1]['summary']='自动浏览约10分钟'
SECTIONS[1]['progress']=''
SECTIONS[2]['summary']='维护商品并获取数据'
SECTIONS[2]['progress']=''

# Preserve read results before a currently unconnected populated-goods brighten checkpoint.
maintenance=BUILTINS['daily_maintenance_v3']['steps']
manual_index=next(i for i,s in enumerate(maintenance) if s['task_type']=='manual')
report_index=next(i for i,s in enumerate(maintenance) if s.get('name')=='整理卖家数据，提醒人工优化与上新')
maintenance.insert(manual_index,maintenance.pop(report_index))

# v4: the free one-key action covers all eligible goods; scroll collection reads all goods.
# Older saved jobs keep their v3 plan and never acquire a new mutation on resume.
BUILTINS['daily_maintenance_v4']={
    'section':'maintain','name':'日常维护','kind':'metrics','min_agent':52,
    'boundary':'执行普通一键擦亮，采集全部在售商品数据，再浏览约10分钟；不进入付费超强擦亮，不修改商品或下单。',
    'steps':phased(START_DAILY,'进入闲鱼')+phased([
        named(tap_text('我的'),'进入我的'),named(text_page('我发布的'),'核对卖家入口'),
        named(tap_text('我发布的'),'进入在售商品'),
        named(observe('在卖','草稿','已下架'),'确认在售商品列表'),
        {'task_type':'scan_goods','parameters':{},'effect':'read','name':'向下翻页，收集全部在售商品数据'},
        route(),named(tap_text('我的'),'返回我的擦亮入口'),named(text_page('我发布的'),'确认卖家页'),
        {'task_type':'brighten_all','parameters':{},'effect':'commit','name':'一键擦亮全部可擦亮商品'}
    ],'商品维护')+phased([route()],'回到首页')+phased(moving_plan(PLAIN_BROWSE),'日常浏览')+FINISH_DAILY}


# Current narrow scope: actual product-detail reading, no seller operations or game entry.
BUILTINS['daily_preheat_v4']={
    'section':'preheat','name':'预热','kind':'research','min_agent':57,
    'boundary':'进入商品查看图文内容，返回列表继续浏览；不留言、不收藏、不下单、不付款。',
    'steps':phased(START_DAILY,'准备')+[{
        'task_type':'content_browse','parameters':{'seconds':600},'effect':'navigation',
        'name':'查看商品内容','phase':'浏览商品'}]
}

# v5 adds explicitly requested collection; saved v4 jobs retain read-only behavior.
BUILTINS['daily_preheat_v5']={
    **BUILTINS['daily_preheat_v4'], 'min_agent':58,
    'boundary':'浏览商品约10分钟并收藏两件，最后寻找低价商品交给专人；不留言、不提交订单、不付款。',
    'steps':phased(START_DAILY,'准备')+[{
        'task_type':'content_browse','parameters':{'seconds':600,'favorites':2,'purchase_handoff':True,'search_browse':True},'effect':'commit',
        'name':'浏览并收藏商品','phase':'预热'}]
}


# Current phone home has two actions. Seller work reuses the measured bulk path;
# buyer work shares the same detail/collection/human-checkout flow as preheat.
BUILTINS['daily_maintenance_v5']={
    'section':'maintain','name':'日常维护','kind':'metrics','min_agent':64,
    'boundary':'普通一键擦亮后浏览商品并收藏两件，最后寻找低价商品供本人确认；不进入付费擦亮、不提交订单、不付款。',
    'steps':[s for s in BUILTINS['daily_maintenance_v4']['steps'] if s.get('phase') in {'进入闲鱼','商品维护','回到首页'}]
        +[{**BUILTINS['daily_preheat_v5']['steps'][-1], 'phase':'买家浏览'}]
}
