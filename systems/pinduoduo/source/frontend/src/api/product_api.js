// 商品管理接口模块（拼多多自动回复系统前端）
// 职责：封装「商品管理」相关后端接口调用，满足需求 15（商品管理）：
//   - 商品列表（后端分页，需求 15.1）；
//   - 触发商品同步（需求 15.2/15.3/15.4）；
//   - 从商品记录创建商品专属回复 / 商品知识（需求 15.5）。
// 说明：统一经 @/utils/request 发起请求，后端地址经环境变量配置（禁止写死 localhost）；
//      签名缺失等业务失败由后端统一响应体返回，request 拦截器经 showToast 统一提示。
import { get, post } from '@/utils/request'

/**
 * 查询某店铺商品列表（后端分页，需求 15.1）
 * @param {Object} params 查询参数：{ shop_pk, page, page_size, status? }
 * @returns {Promise<Object>} 分页结构 { list, total, page, page_size }
 */
export function listProducts(params) {
  return get('/products', params)
}

/**
 * 查看单个商品详情（默认读库；refreshSpecs=true 时补充实时规格）
 * @param {number} productId 商品记录主键
 * @returns {Promise<Object>} 商品详情 { ...基础字段, specifications, detail_message }
 */
export function getProductDetail(productId, refreshSpecs = false) {
  return get(`/products/${productId}/detail`, { refresh_specs: refreshSpecs }, { timeout: 12000 })
}

/**
 * 触发商品同步（需求 15.2/15.3）
 * @param {number} shopPk 店铺主键 shop.id
 * @returns {Promise<Object>} { synced, total }
 */
export function syncProducts(shopPk) {
  return post('/products/sync-jobs', { shop_pk: shopPk }, { timeout: 12000 })
}

export function refreshProduct(productId) {
  return post(`/products/${productId}/refresh`, {}, { timeout: 12000 })
}

export function getSyncStatus(shopPk) {
  return get('/products/sync-status', { shop_pk: shopPk }, { timeout: 12000 })
}

/**
 * 从商品记录创建商品专属回复（需求 15.5）
 * @param {number} productId 商品记录主键
 * @param {Object} payload { reply_content, reply_type?, enabled? }
 * @returns {Promise<Object>} 保存后的商品专属回复
 */
export function createGoodsReplyFromProduct(productId, payload) {
  return post(`/products/${productId}/goods-reply`, payload)
}

/**
 * 从商品记录创建 / 更新商品知识（需求 15.5）
 * @param {number} productId 商品记录主键
 * @param {Object} payload { extracted_content?, specifications? }
 * @returns {Promise<Object>} 保存后的商品知识
 */
export function createKnowledgeFromProduct(productId, payload) {
  return post(`/products/${productId}/knowledge`, payload)
}

export default {
  listProducts,
  getProductDetail,
  syncProducts,
  refreshProduct,
  getSyncStatus,
  createGoodsReplyFromProduct,
  createKnowledgeFromProduct,
}
