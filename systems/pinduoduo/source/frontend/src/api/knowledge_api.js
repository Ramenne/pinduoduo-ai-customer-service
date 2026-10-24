// 知识库接口模块（拼多多自动回复系统前端）
// 职责：封装「商品知识库」与「客服知识库（含批量导入）」相关 REST 调用，供知识库页面复用。
// 对应后端路由：backend/app/api/routes/knowledge.py（需求 9 / 10）。
// 说明：统一经 @/utils/request 发起请求，成功返回后端 data，失败已统一 showToast 提示；
//       后端业务前缀 /api/v1，基址 /api 由 request 提供；禁止写死 localhost（规范 21）。
import { get, post, put, del } from '@/utils/request'


// ======================== 商品知识库（需求 9） ========================

// 商品知识列表（后端分页，需求 9.3）。shop_pk 必填。
export function listProductKnowledge(params) {
  return get(`/product-knowledge`, params)
}

// 新增 / 更新商品知识（按 (shop_pk, goods_id) upsert 幂等，需求 9.1/9.2）。
export function upsertProductKnowledge(payload) {
  return post(`/product-knowledge`, payload)
}

// 修改商品知识字段。
export function updateProductKnowledge(itemId, payload) {
  return put(`/product-knowledge/${itemId}`, payload)
}

// 启用 / 停用商品知识（status：1=启用，0=停用）。
export function setProductKnowledgeStatus(itemId, status) {
  return put(`/product-knowledge/${itemId}/status`, { status })
}

// 逻辑删除商品知识（需求 9.5，禁止物理删除）。
export function deleteProductKnowledge(itemId) {
  return del(`/product-knowledge/${itemId}`)
}

// ======================== 客服知识库（需求 10） ========================

// 客服知识列表（后端分页，需求 10.6）。shop_pk 必填。
export function listCsKnowledge(params) {
  return get(`/cs-knowledge`, params)
}

// 新增客服知识（需求 10.1）。
export function createCsKnowledge(payload) {
  return post(`/cs-knowledge`, payload)
}

// 批量导入客服知识（跳过同店铺内标题与内容完全相同的重复项，返回成功/跳过数量，需求 10.2）。
export function importCsKnowledge(payload) {
  return post(`/cs-knowledge/import`, payload)
}

// 修改客服知识字段。
export function updateCsKnowledge(itemId, payload) {
  return put(`/cs-knowledge/${itemId}`, payload)
}

// 启用 / 停用客服知识（enabled：true=启用，false=停用）。
export function setCsKnowledgeStatus(itemId, enabled) {
  return put(`/cs-knowledge/${itemId}/status`, { enabled })
}

// 逻辑删除客服知识（禁止物理删除）。
export function deleteCsKnowledge(itemId) {
  return del(`/cs-knowledge/${itemId}`)
}

// 人工沉淀复用同一知识后端，不另建模型/检索通道。
export const listCurationTasks = params => get('/knowledge-curation/tasks', params)
export const listCurationProducts = params => get('/knowledge-curation/products', params)
export const previewCuration = (task, index, params) => get(`/knowledge-curation/tasks/${task}/candidates/${index}/preview`, params)
export const reviewCuration = (task, index, payload) => post(`/knowledge-curation/tasks/${task}/candidates/${index}/review`, payload)

export const listKnowledgeVersions = (kind, id, page = 1) => get(`/knowledge-versions/${kind}/${id}`, { page })
export const getKnowledgeVersion = (kind, id, version) => get(`/knowledge-versions/${kind}/${id}/${version}`)
export const restoreKnowledgeVersion = (kind, id, version, expectedHash) => post(`/knowledge-versions/${kind}/${id}/${version}/restore`, { expected_hash: expectedHash })
