// 公告接口模块（拼多多自动回复系统前端）
// 职责：封装公告管理与用户端展示相关后端接口调用（需求 21.3），覆盖管理端新增、
//       编辑、启停用、逻辑删除、列表分页与用户端可见公告展示。
// 说明：统一经 @/utils/request 发起，后端分页（规范 28）；管理端接口为管理员专属，
//       用户端展示对所有登录用户开放（需求 21.3/21.17）。
import { get, post, put, del } from '@/utils/request'

// 用户端公告展示（仅启用且未删除，后端分页）
export function listVisibleAnnouncements(params) {
  return get('/announcements/visible', params)
}

// 管理端公告列表（后端分页）
export function listAnnouncements(params) {
  return get('/announcements', params)
}

// 公告详情
export function getAnnouncement(annId) {
  return get(`/announcements/${annId}`)
}

// 新增公告
export function createAnnouncement(payload) {
  return post('/announcements', payload)
}

// 编辑公告
export function updateAnnouncement(annId, payload) {
  return put(`/announcements/${annId}`, payload)
}

// 启停用公告
export function setAnnouncementStatus(annId, enabled) {
  return put(`/announcements/${annId}/status`, { enabled })
}

// 逻辑删除公告
export function deleteAnnouncement(annId) {
  return del(`/announcements/${annId}`)
}
