// 接口模块入口：集中导出各业务 API 调用方法
// 统一使用 @/utils/request 发起请求，后端地址经环境变量配置（禁止写死 localhost）。
// 各业务接口按模块拆分实现，统一从此处以命名空间导出，便于按需引入。
// 用法示例：import { shopApi, keywordApi, replyApi, chatApi, productApi } from '@/api'

// 认证与个人设置接口（登录/登出、账户信息/修改密码/联系方式、登录页品牌）
export * as authApi from './auth_api'
// 店铺与账号管理（任务 17.2）+ 各页面按店铺筛选下拉复用
export * as shopApi from './shop_api'
// 关键词规则（任务 17.2）
export * as keywordApi from './keyword_api'
// 默认与商品专属回复（任务 17.2）
export * as replyApi from './reply_api'
// 知识库（商品知识 / 客服知识）
export * as knowledgeApi from './knowledge_api'
// 自动回复设置（AI / 营业时间 / 过滤黑名单 / 风控 / 转人工）
export * as autoReplyApi from './auto_reply_api'
// 在线聊天
export * as chatApi from './chat_api'
// 商品管理
export * as productApi from './product_api'
