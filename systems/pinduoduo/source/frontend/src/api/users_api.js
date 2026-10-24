// 用户与角色管理接口模块（拼多多自动回复系统前端）
// 职责：封装用户与角色管理相关后端接口调用（需求 2），覆盖创建用户、用户列表、
//       改角色、启停用、角色列表查询。
// 说明：统一经 @/utils/request 发起，后端分页（规范 28）；接口为管理员/授权用户可访问，
//       未授权由后端返回「无访问权限」（需求 2.4）。
import { get, post, put } from '@/utils/request'

// 用户列表（后端分页）
export function listUsers(params) {
  return get('/users', params)
}

// 创建用户并指定角色
export function createUser(payload) {
  return post('/users', payload)
}

// 修改用户角色
export function updateUserRole(userId, roleId) {
  return put(`/users/${userId}/role`, { role_id: roleId })
}

// 启用 / 停用用户（停用为逻辑删除）
export function updateUserStatus(userId, enabled) {
  return put(`/users/${userId}/status`, { enabled })
}

// 角色列表（后端分页）
export function listRoles(params) {
  return get('/roles', params)
}

// 查询单个角色
export function getRole(roleId) {
  return get(`/roles/${roleId}`)
}

// 新增角色
export function createRole(payload) {
  return post('/roles', payload)
}

// 修改角色（角色名 / 启停用）
export function updateRole(roleId, payload) {
  return put(`/roles/${roleId}`, payload)
}

// 启用 / 停用角色
export function updateRoleStatus(roleId, enabled) {
  return put(`/roles/${roleId}/status`, { enabled })
}

// 设为默认注册角色
export function setDefaultRole(roleId) {
  return put(`/roles/${roleId}/default`)
}

// 查询角色已授予权限 id 列表
export function getRolePermissions(roleId) {
  return get(`/roles/${roleId}/permissions`)
}

// 重设角色权限集合
export function assignRolePermissions(roleId, permissionIds) {
  return put(`/roles/${roleId}/permissions`, { permission_ids: permissionIds })
}

// 权限点列表（按资源分组，供权限分配勾选）
export function listPermissions() {
  return get('/permissions')
}

// 查询当前用户菜单授权资源（is_admin + 被授予 view 的资源键列表）
export function getMyMenuResources() {
  return get('/me/menu-resources')
}
