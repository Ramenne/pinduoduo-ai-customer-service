// 布局与主题状态管理 store：拼多多自动回复系统前端（任务 16.2）
// 职责：集中管理与界面布局/主题相关的状态（规范 25 / 26）：
//   1. 暗黑模式开关（darkMode）；
//   2. 主题色预设（themeColor）；
//   3. 左侧导航栏的收起/展开（sidebarCollapsed，桌面端）与移动端抽屉显隐（sidebarMobileOpen）。
// 颜色/类名的实际应用委托给 utils/theme.js（仅切换 CSS 变量预设与 .dark 类），本 store 仅维护响应式状态。
// 说明：全局加载遮罩与 Toast 提示由 ui_store.js（useUIStore）管理，二者职责分离。
import { defineStore } from 'pinia'
import {
  applyDarkMode,
  applyThemeColor,
  initializeTheme,
  isDarkMode,
  getStoredThemeColor,
} from '@/utils/theme'

export const useLayoutStore = defineStore('layout', {
  state: () => ({
    // 是否暗黑模式（初始值以 <html> 当前类与 localStorage 为准）
    darkMode: isDarkMode(),
    // 当前主题色预设 key（默认蓝白主色调）
    themeColor: getStoredThemeColor(),
    // 桌面端左侧导航是否收起（规范 25：导航栏支持显示/隐藏）
    sidebarCollapsed: false,
    // 移动端左侧导航抽屉是否打开（响应式适配，规范 20）
    sidebarMobileOpen: false,
  }),
  actions: {
    // 应用启动时初始化主题（暗黑模式 + 主题色），并同步到 state
    initTheme() {
      const { dark, color } = initializeTheme()
      this.darkMode = dark
      this.themeColor = color
    },
    // 设置暗黑模式
    setDarkMode(dark) {
      this.darkMode = applyDarkMode(dark)
    },
    // 切换暗黑模式（亮 <-> 暗）
    toggleDarkMode() {
      this.darkMode = applyDarkMode(!this.darkMode)
    },
    // 设置主题色预设（规范 26）
    setThemeColor(colorKey) {
      this.themeColor = applyThemeColor(colorKey)
    },
    // 切换桌面端导航收起/展开
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
    },
    // 设置桌面端导航收起状态
    setSidebarCollapsed(collapsed) {
      this.sidebarCollapsed = collapsed
    },
    // 设置移动端导航抽屉显隐
    setSidebarMobileOpen(open) {
      this.sidebarMobileOpen = open
    },
  },
})
