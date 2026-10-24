# 原系统开发与部署

只体验产品请从根目录 `npm run demo` 开始。下方是真实源码的开发入口，需要独立配置。

## 拼多多管理系统

Python 3.11+、Node.js 18+、MySQL 8、Redis 7。

```bash
cd systems/pinduoduo/source/frontend
npm ci --ignore-scripts
npm run build
```

后端三个服务位于source/backend、source/websocket、source/scheduler，配置模板和结构SQL分别位于子系统config与database目录。详细步骤见 [原README](../systems/pinduoduo/source/README.md) 和 [复现说明](../systems/pinduoduo/复现说明.md)。原README顶部版本说明停留在较早迭代，当前导出依据以VERSION.json为准。

首次启动前必须生成自己的JWT、数据加密与内部服务密钥，并在隔离数据库初始化；平台接入只使用本人有权管理的测试店铺。

## 闲鱼管理系统

见 [原README](../systems/xianyu/source/README.md) 和 [复现说明](../systems/xianyu/复现说明.md)。建议从本地源码构建与配置开始。原README中的远程安装地址已脱敏，不能复制后直接执行；原作者发布镜像也不能当成本仓库源码的已验证构建产物。

## 闲鱼手机端

源码在 `systems/xianyu/source/xianyu-mobile`，运行与构建步骤见 [手机端README](../systems/xianyu/source/xianyu-mobile/README.md)。采用Expo / React Native；按该目录声明准备Node.js、JDK和Android SDK，通过服务器配置页设置自己的闲鱼服务地址，再用自己的测试账号登录。

手机端通过API和WebSocket协作，不需要另复制一份店铺数据库。本次未执行手机原生包构建或真机联调；不能用Web演示截图代替手机端验收。

## Android助手

见 [复现说明](../systems/android-agent/复现说明.md)。控制面需Python 3.11+；Android构建需JDK17、SDK35及原Gradle配置。设备接入需可信HTTPS、自己的服务地址及新设备绑定，签名与生产APK不随公开仓库发布。

## 验证记录

2026-10-24：拼多多原前端 `npm ci --ignore-scripts`、`npm run build` 通过；独立展示的登录、人工接管、模拟回复通过浏览器检查。

未执行真实店铺登录、平台消息收发、完整后台部署、数据库恢复或Android真机验收。历史捕获的页面fixture未公开，部分依赖它们的回放脚本不能直接运行；可使用自己的授权测试数据重新生成。
