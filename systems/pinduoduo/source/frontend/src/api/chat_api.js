// 在线聊天接口模块（拼多多自动回复系统前端）
// 职责：封装「在线聊天」相关后端接口调用，满足需求 14（在线聊天）与需求 17（会话订单上下文）：
//   - 会话列表（后端分页，需求 14.1）；
//   - 会话历史消息（北京时间，需求 14.2）；
//   - 手动发送消息（需求 14.3）；
//   - 新消息提示数据（需求 14.4）；
//   - 会话订单/商品上下文展示（需求 17.3/17.4/17.5）。
// 说明：统一经 @/utils/request 发起请求，后端地址经环境变量配置（禁止写死 localhost）；
//      请求成功后返回后端响应体的 data 部分（由 request 响应拦截器统一解包）。
import { get, post } from '@/utils/request'

/**
 * 查询会话列表（后端分页，需求 14.1）
 * @param {Object} params 查询参数：{ shop_pk?, page, page_size }
 * @returns {Promise<Object>} 分页结构 { list, total, page, page_size }
 */
export function listConversations(params) {
  return get('/chat/conversations', params)
}

/**
 * 查询某会话历史消息（北京时间正序，需求 14.2）
 * @param {number} conversationId 会话主键
 * @param {Object} params 分页参数：{ page, page_size }
 * @returns {Promise<Object>} 分页结构 { list, total, page, page_size }
 */
export function listMessages(conversationId, params) {
  return get(`/chat/conversations/${conversationId}/messages`, params)
}

/**
 * 在某会话中手动发送消息（需求 14.3）
 * @param {number} conversationId 会话主键
 * @param {string} content 待发送的消息内容
 * @returns {Promise<Object>} { sent: true }
 */
export function sendMessage(conversationId, content) {
  return post(`/chat/conversations/${conversationId}/send`, { content })
}

/**
 * 获取新消息提示数据（未读汇总，需求 14.4）
 * @returns {Promise<Object>} { total_unread, conversations: [...] }
 */
export function getNewMessageHints() {
  return get('/chat/hints')
}

/**
 * 展示某会话已记录的订单/商品上下文（需求 17.3/17.4/17.5）
 * @param {number} conversationId 会话主键
 * @returns {Promise<Object>} { conversation, latest_order_context, latest_goods_context, context_messages }
 */
export function getConversationContext(conversationId) {
  return get(`/chat/conversations/${conversationId}/context`)
}

/**
 * 实时同步某店铺的拼多多会话列表（方案 A：实时调拼多多接口，需求 14.1）
 * @param {number} shopPk 店铺主键 shop.id
 * @param {Object} params 可选参数：{ fetch_all }
 * @returns {Promise<Object>} { shop_pk, conversations: [...] }
 */
export function syncConversations(shopPk, params) {
  return get(`/chat/shops/${shopPk}/sync-conversations`, params)
}

/**
 * 实时同步某客户会话的全部历史聊天记录并落库（方案 A，需求 14.2/17）
 * @param {number} shopPk 店铺主键 shop.id
 * @param {string} customerUid 客户唯一标识
 * @returns {Promise<Object>} { conversation_id, messages: [...], persisted }
 */
export function syncHistory(shopPk, customerUid) {
  return post('/chat/sync-history', { shop_pk: shopPk, customer_uid: customerUid })
}

/**
 * 在线聊天店铺列表（含实时连接状态，后端分页，需求 14.1）
 * @param {Object} params 分页参数：{ page, page_size }
 * @returns {Promise<Object>} 分页结构 { list:[{shop_pk, shop_id, shop_name, connected, status}], total, page, page_size }
 */
export function listChatShops(params) {
  return get('/chat/shops', params)
}

/**
 * 连接指定店铺的拼多多长连接（支持多店铺同时连接，需求 5.1）
 * @param {number} shopPk 店铺主键 shop.id
 * @returns {Promise<Object>} { connected: true }
 */
export function connectShop(shopPk) {
  return post(`/chat/shops/${shopPk}/connect`)
}

/**
 * 断开指定店铺的拼多多长连接（需求 3.5）
 * @param {number} shopPk 店铺主键 shop.id
 * @returns {Promise<Object>} { connected: false }
 */
export function disconnectShop(shopPk) {
  return post(`/chat/shops/${shopPk}/disconnect`)
}

/**
 * 按 (店铺, 客户 uid) 手动发送消息（实时会话直接发送，需求 14.3）
 * @param {number} shopPk 店铺主键 shop.id
 * @param {string} customerUid 客户唯一标识
 * @param {string} content 待发送的消息内容
 * @returns {Promise<Object>} { sent: true }
 */
export function sendMessageByUid(shopPk, customerUid, content, operationKey) {
  return post('/chat/send-by-uid', { shop_pk: shopPk, customer_uid: customerUid, content, operation_key: operationKey })
}

export function getDeliveries(shopPk, customerUid) {
  return get('/chat/workspace/deliveries', { shop_pk: shopPk, customer_uid: customerUid })
}
export function resolveDelivery(shopPk, customerUid, deliveryId, outcome, note) {
  return post('/chat/workspace/deliveries/resolve', { shop_pk: shopPk, customer_uid: customerUid, delivery_id: deliveryId, outcome, note })
}

export default {
  listConversations,
  listMessages,
  sendMessage,
  getNewMessageHints,
  getConversationContext,
  syncConversations,
  syncHistory,
  listChatShops,
  connectShop,
  disconnectShop,
  sendMessageByUid,
}

export * from './chat_workspace_api'
