// 标签页（tabs）状态管理 store：拼多多自动回复系统前端（任务 16.2）
// 职责：维护顶部导航栏的标签页集合与当前激活标签（规范 31：每打开一个菜单顶部需有 tabs/面包屑）。
//   - 打开菜单时自动新增标签（已存在则仅激活）；
//   - 支持关闭单个标签、关闭右侧、关闭左侧、关闭其它、关闭全部（参照参考项目 xianyu-auto-reply-wangpan 顶部导航右键操作）；
//   - 首页（仪表盘）标签固定保留、不可关闭。
// 说明：标签的标题取自路由 meta.title（由 router 配置，与 config/navigation.js 菜单同源保持一致）。
import { defineStore } from 'pinia'

// 首页标签路径（固定保留、不可关闭）：与路由默认落地页 /dashboard 保持一致。
const HOME_PATH = '/dashboard'
// 对外暴露首页标签路径，供组件（如右键菜单禁用「关闭当前」）复用，避免各处硬编码。
export const TABS_HOME_PATH = HOME_PATH

export const useTabsStore = defineStore('tabs', {
  state: () => ({
    // 标签集合：{ path, title, closable }
    tabs: [{ path: HOME_PATH, title: '仪表盘', closable: false }],
    // 当前激活标签路径
    activeTab: HOME_PATH,
  }),
  actions: {
    // 新增或激活标签（已存在仅激活，不重复添加）
    addTab(tab) {
      if (!tab || !tab.path) {
        return
      }
      const exists = this.tabs.find((t) => t.path === tab.path)
      if (!exists) {
        this.tabs.push({
          path: tab.path,
          title: tab.title || tab.path,
          // 首页不可关闭，其余可关闭
          closable: tab.path !== HOME_PATH,
        })
      }
      this.activeTab = tab.path
    },
    // 关闭单个标签，返回关闭后应导航到的路径（若关闭的是当前激活标签）
    removeTab(path) {
      if (path === HOME_PATH) {
        // 首页不可关闭
        return null
      }
      const index = this.tabs.findIndex((t) => t.path === path)
      if (index === -1) {
        return null
      }
      this.tabs.splice(index, 1)
      // 仅当关闭的是当前激活标签时才需要切换激活标签
      if (this.activeTab === path) {
        // 优先激活右侧相邻标签，否则左侧相邻标签
        const next = this.tabs[index] || this.tabs[index - 1] || this.tabs[this.tabs.length - 1]
        this.activeTab = next ? next.path : HOME_PATH
        return this.activeTab
      }
      return null
    },
    // 关闭除指定标签与首页外的其它标签
    removeOtherTabs(path) {
      this.tabs = this.tabs.filter((t) => t.path === HOME_PATH || t.path === path)
      this.activeTab = path
    },
    // 关闭指定标签右侧的全部标签（保留首页与该标签及其左侧）。
    // 返回关闭后应导航到的路径（若当前激活标签被关闭则切到该标签，否则返回 null）。
    removeTabsToRight(path) {
      const index = this.tabs.findIndex((t) => t.path === path)
      if (index === -1) {
        return null
      }
      // 记录当前激活标签在裁剪前的位置，用于判断其是否被关闭
      const activeIndex = this.tabs.findIndex((t) => t.path === this.activeTab)
      // 仅保留 [0, index] 区间的标签（含指定标签）
      this.tabs = this.tabs.slice(0, index + 1)
      // 当前激活标签位于被关闭区域（index 右侧）时，激活切换到指定标签
      if (activeIndex > index) {
        this.activeTab = path
        return path
      }
      return null
    },
    // 关闭指定标签左侧的全部标签（始终保留首页与该标签及其右侧）。
    // 返回关闭后应导航到的路径（若当前激活标签被关闭则切到该标签，否则返回 null）。
    removeTabsToLeft(path) {
      const index = this.tabs.findIndex((t) => t.path === path)
      if (index === -1) {
        return null
      }
      const activeIndex = this.tabs.findIndex((t) => t.path === this.activeTab)
      const homeTab = this.tabs.find((t) => t.path === HOME_PATH)
      // 保留首页 + 指定标签及其右侧（去重首页，避免首页重复出现）
      const rest = this.tabs.slice(index).filter((t) => t.path !== HOME_PATH)
      this.tabs = homeTab ? [homeTab, ...rest] : rest
      // 当前激活标签位于被关闭区域（index 左侧且非首页）时，激活切换到指定标签
      if (activeIndex < index && this.activeTab !== HOME_PATH) {
        this.activeTab = path
        return path
      }
      return null
    },
    // 关闭全部（保留首页）
    removeAllTabs() {
      this.tabs = this.tabs.filter((t) => t.path === HOME_PATH)
      this.activeTab = HOME_PATH
      return HOME_PATH
    },
    // 设置当前激活标签
    setActiveTab(path) {
      this.activeTab = path
    },
  },
})
