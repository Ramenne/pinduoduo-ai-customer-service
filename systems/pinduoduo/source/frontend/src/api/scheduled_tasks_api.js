// 定时任务接口模块（拼多多自动回复系统前端）
// 职责：封装定时任务管理与执行日志相关后端接口调用（需求 21.2），覆盖任务列表、
//       更新调度配置、启停用与执行日志查询。
// 说明：统一经 @/utils/request 发起，后端分页（规范 28）；定时任务为管理员专属，
//       未授权由后端返回「无访问权限」（需求 21.17）。
import { get, put } from '@/utils/request'

// 定时任务列表（后端分页）
export function listScheduledTasks(params) {
  return get('/scheduled-tasks', params)
}

// 更新定时任务的调度方式 / 配置 / 启停用
export function updateScheduledTask(taskId, payload) {
  return put(`/scheduled-tasks/${taskId}`, payload)
}

// 启停用定时任务
export function setScheduledTaskStatus(taskId, enabled) {
  return put(`/scheduled-tasks/${taskId}/status`, { enabled })
}

// 定时任务执行日志列表（后端分页）
export function listTaskRunLogs(params) {
  return get('/scheduled-tasks/run-logs', params)
}
