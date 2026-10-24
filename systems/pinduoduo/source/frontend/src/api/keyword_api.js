// 关键词规则接口模块（拼多多自动回复系统前端）
// 职责（需求 6.1/6.6/6.7）：封装 backend 「关键词自动回复规则」REST 接口调用。
// 说明：统一经 @/utils/request 发起，成功返回后端 data，失败已统一 showToast 提示；
//       后端地址经环境变量配置（禁止写死 localhost，规范 21）。
import { get, post, put, del } from '@/utils/request'

// 查询关键词规则列表（后端分页，需求 6.6）
// @param {{page:number, page_size:number, shop_pk?:number, enabled?:boolean}} params
export function fetchKeywordRules(params) {
  return get('/keywords', params)
}

// 查询单条关键词规则
export function fetchKeywordRule(ruleId) {
  return get(`/keywords/${ruleId}`)
}

// 创建关键词规则（需求 6.1）
// payload: { shop_pk, keyword, match_type, reply_content, reply_type?, priority?, enabled? }
export function createKeywordRule(payload) {
  return post('/keywords', payload)
}

// 修改关键词规则（仅更新传入的非空字段）
// payload: { keyword?, match_type?, reply_type?, reply_content?, priority? }
export function updateKeywordRule(ruleId, payload) {
  return put(`/keywords/${ruleId}`, payload)
}

// 启用 / 停用关键词规则（下一条消息生效，需求 6.7）
export function setKeywordRuleStatus(ruleId, enabled) {
  return put(`/keywords/${ruleId}/status`, { enabled })
}

// 逻辑删除关键词规则（禁止物理删除，规范 11）
export function deleteKeywordRule(ruleId) {
  return del(`/keywords/${ruleId}`)
}
