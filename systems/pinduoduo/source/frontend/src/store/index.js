// 状态管理入口：集中导出各业务 Pinia store
// 各业务 store 按模块拆分实现，统一从此处导出，便于按需引入。
// 用法示例：import { useUserStore, useLayoutStore } from '@/store'

// 当前登录用户状态（含角色与授权菜单）
export { useUserStore } from './user_store'
// 全局 UI 状态（Toast 提示队列 + 全局加载遮罩）
export { useUIStore } from './ui_store'
// 布局与主题状态（暗黑模式 / 主题色 / 导航显隐）
export { useLayoutStore } from './layout_store'
// 顶部标签页（tabs）状态
export { useTabsStore } from './tabs_store'
