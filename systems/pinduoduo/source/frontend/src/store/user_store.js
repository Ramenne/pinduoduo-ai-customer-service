// 当前登录用户状态管理（Pinia store）
// 职责（需求 1/2.5/2.6/2.9/22）：
//   1. 维护当前登录用户的基本信息、角色与被授权的资源键列表；
//   2. 维护登录令牌（token）并与 localStorage 同步，支持刷新页面后恢复登录态；
//   3. 提供 isAdmin（是否管理员）与 authorizedResources（被授权资源键）供菜单按权限渲染；
//   4. 管理员对菜单强制可见的语义由 navigation.js 的过滤函数统一处理（本 store 仅提供 isAdmin 判定）。
// 说明：用户信息在登录后由后端接口返回并写入本 store；授权资源经 loadAuthorizedResources
//      调用 /me/menu-resources 获取。
//      authorizedResources 为 null 表示「授权信息尚未加载」，此时菜单过滤函数不做授权限制（全部放行），
//      避免登录初始化阶段菜单闪烁为空。
//      令牌与用户信息的 localStorage 键名复用 api/auth_api.js 的约定，并与 utils/request.js 一致。
import { defineStore } from 'pinia'
import { TOKEN_STORAGE_KEY, USER_STORAGE_KEY } from '@/api/auth_api'

// 管理员角色标识（与后端角色约定一致：序列化用户时附带 is_admin 布尔值）
const ADMIN_ROLE = 'admin'

// 从 localStorage 安全读取并解析已存用户信息（解析失败回退 null，不抛错）
function readStoredUser() {
  const raw = localStorage.getItem(USER_STORAGE_KEY)
  if (!raw) {
    return null
  }
  // 解析失败说明本地数据损坏：捕获异常按未登录处理，避免 store 初始化抛错导致白屏
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    // 本地数据损坏：清理脏数据并按未登录处理
    localStorage.removeItem(USER_STORAGE_KEY)
    return null
  }
}

export const useUserStore = defineStore('user', {
  state: () => ({
    // 登录令牌；为空表示未登录（初始值从 localStorage 恢复）
    token: localStorage.getItem(TOKEN_STORAGE_KEY) || '',
    // 用户基本信息（用户名、角色名、联系方式等）；未登录为 null（初始值从 localStorage 恢复）
    userInfo: readStoredUser(),
    // 被授权的资源键列表（被授予 view 的资源，需求 2.6）；null 表示尚未加载
    authorizedResources: null,
  }),
  getters: {
    // 是否已登录：以是否持有令牌为准
    isLoggedIn: (state) => Boolean(state.token),
    // 当前角色中文/标识展示用：优先后端返回的 role_name
    roleName: (state) => (state.userInfo && state.userInfo.role_name) || '',
    // 是否管理员（需求 2.9 管理员专属菜单强制可见的判定依据）
    // 后端 serialize_user 在含角色时返回 is_admin 布尔值；兼容 role 字段为 'admin' 的情况。
    isAdmin: (state) => {
      const info = state.userInfo
      if (!info) {
        return false
      }
      if (typeof info.is_admin === 'boolean') {
        return info.is_admin
      }
      return info.role === ADMIN_ROLE
    },
  },
  actions: {
    // 登录成功后写入令牌与用户信息，并同步到 localStorage（需求 1.1）
    setLogin(token, userInfo) {
      this.token = token || ''
      this.userInfo = userInfo || null
      if (token) {
        localStorage.setItem(TOKEN_STORAGE_KEY, token)
      } else {
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      }
      if (userInfo) {
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userInfo))
      } else {
        localStorage.removeItem(USER_STORAGE_KEY)
      }
    },
    // 单独更新用户信息（如个人设置保存联系方式后刷新展示），并同步到 localStorage
    setUserInfo(userInfo) {
      this.userInfo = userInfo || null
      if (userInfo) {
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userInfo))
      } else {
        localStorage.removeItem(USER_STORAGE_KEY)
      }
    },
    // 设置被授权的资源键列表（需求 2.6）
    setAuthorizedResources(resources) {
      if (resources === null || resources === undefined) {
        this.authorizedResources = null
        return
      }
      const list = Array.isArray(resources) ? resources : []
      this.authorizedResources = Array.from(new Set(list.filter(Boolean)))
    },
    // 拉取并写入当前用户的菜单授权资源（登录 / 注册 / 刷新页面后调用，需求 2.6）。
    // 管理员对菜单强制可见由 navigation.js 按 isAdmin 处理，本列表仅约束普通用户。
    async loadAuthorizedResources() {
      // 动态导入避免 store 与 api 的循环依赖。
      const { getMyMenuResources } = await import('@/api/users_api')
      const data = await getMyMenuResources()
      this.setAuthorizedResources((data && data.resources) || [])
      return this.authorizedResources
    },
    // 清空用户状态（登出 / 令牌失效时调用），并清理 localStorage
    clearUser() {
      this.token = ''
      this.userInfo = null
      this.authorizedResources = null
      localStorage.removeItem(TOKEN_STORAGE_KEY)
      localStorage.removeItem(USER_STORAGE_KEY)
    },
  },
})
