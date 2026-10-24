# 工作流程与代码入口

## 拼多多

顾客消息 → websocket通道 → 消息记录与回复引擎 → 商品／客服知识检索与工具调用 → AI或人工回复 → 发送操作及结果状态。

- `systems/pinduoduo/source/frontend/src/pages/online_chat.vue`：会话、人工操作与AI协作入口；具体文件名以pages目录为准。
- `systems/pinduoduo/source/websocket/agent`：LLM接入、工具调用及回复上下文。
- `systems/pinduoduo/source/websocket/channel_pdd`：连接、消息、店铺身份与平台操作。
- `systems/pinduoduo/source/backend`：知识、店铺、会话等业务API。
- `systems/pinduoduo/source/common`：共享模型、知识服务及数据访问。
- `systems/pinduoduo/database`：结构SQL，不含生产行数据。

人工补充与实际发送分开；知识写入需要考虑来源、适用范围和版本；恢复处理需要核对消息的业务状态，不能只看连接是否在线。

## 闲鱼

`systems/xianyu/source` 保留原多服务结构：frontend、backend-web、websocket、scheduler与common。它有自己的店铺、连接与业务配置，不把拼多多中某项功能默认视为已在闲鱼具有相同实现。

## 闲鱼手机端与Web端协作

`systems/xianyu/source/xianyu-mobile` 是完整的Expo / React Native手机管理端，不是Android设备助手。手机端保存用户选择的服务地址，通过 `api/wrappers/client.ts` 调用对应闲鱼后台，`lib/ws.ts` 建立账号级WebSocket并处理前台恢复与重连。

商家既可以在Web端管理业务，也可以在手机进入消息、订单、商品和账号等页面。它们连接同一目标闲鱼服务时，业务数据以该服务为准；配置不同服务地址不等于自动打通不同服务的数据。

| 服务 | 导出源码中的默认端口／接入 |
| --- | --- |
| 闲鱼Web前端 | 9000 |
| 闲鱼业务API | 8089 |
| 闲鱼消息服务 | 8090 |
| 闲鱼调度服务 | 8091 |
| 闲鱼手机端 | 配置闲鱼服务器地址；没有独立生产数据库 |
| Android助手控制面 | 开发默认18080，手机接入需可信HTTPS |

这些端口服务于不同职责，不是六个互不相关的产品。实际公开入口和路由应按自己的部署配置。

## Android助手

`systems/android-agent/source/apps/android-agent` 是Android原生入口；`source/services/control-plane` 管理账号、设备授权、任务和结果。与闲鱼后台附带的xianyu-mobile不是同一客户端。

设备执行与云端任务状态分开确认；结果outbox和绑定等机制用于处理断网、重连和重复回传。新部署需要重新注册账号和绑定设备，不复用旧生产签名。

## 展示原型

`demo` 独立实现静态合成会话及页面内交互，没有调用上述服务。它用于说明产品与人机分工，不作为原系统实现和效果的证据。
