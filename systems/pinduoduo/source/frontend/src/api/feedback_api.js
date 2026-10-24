// 意见反馈接口模块（拼多多自动回复系统前端）
// 职责：封装意见反馈相关后端接口调用（需求 21.5），覆盖用户端提交、查看本人反馈，
//       管理端反馈列表、详情与处理回复。
// 说明：统一经 @/utils/request 发起，后端分页（规范 28）；提交与查看本人反馈对所有
//       登录用户开放，列表/详情/处理回复为管理员专属（需求 21.17）。
import { get, post, put } from '@/utils/request'

// 提交意见反馈
export function submitFeedback(payload) {
  return post('/feedbacks', payload)
}

// 查看本人反馈列表（后端分页）
export function listMyFeedbacks(params) {
  return get('/feedbacks/mine', params)
}

// 管理端反馈列表（后端分页）
export function listFeedbacks(params) {
  return get('/feedbacks', params)
}

// 反馈详情
export function getFeedback(fbId) {
  return get(`/feedbacks/${fbId}`)
}

// 处理回复反馈
export function replyFeedback(fbId, payload) {
  return put(`/feedbacks/${fbId}/reply`, payload)
}
