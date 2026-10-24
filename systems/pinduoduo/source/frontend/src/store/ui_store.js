// 全局 UI 状态管理（Pinia）
// 职责：集中管理通用 UI 状态，目前包含：
//   1. toasts：消息提示队列，供 Toast 组件渲染（替代 alert，规范 6）；
//   2. globalLoading：全局加载遮罩开关，供 Loading 组件展示遮罩 + 转圈（规范 23）。
// 说明：Toast 组件挂载后会通过 utils/toast.js 的 registerToastHandler 注入提示能力，
//       使 request.js 等模块经 showToast 调用最终落到本 store 的 addToast。
import { defineStore } from 'pinia'

// 自增 ID，用于区分每条提示
let toastSeed = 0

export const useUIStore = defineStore('ui', {
  state: () => ({
    // 当前展示的提示列表：{ id, message, type, duration }
    toasts: [],
    // 全局加载遮罩计数（>0 表示需要展示遮罩 + 转圈），用计数支持并发请求叠加
    loadingCount: 0,
  }),
  getters: {
    // 是否展示全局加载遮罩
    globalLoading: (state) => state.loadingCount > 0,
  },
  actions: {
    /**
     * 新增一条消息提示，并在 duration 毫秒后自动移除
     * @param {string} message 中文提示文案
     * @param {string} [type] 类型：success/error/warning/info
     * @param {number} [duration] 自动消失毫秒数，默认 3000
     * @returns {number} 新增提示的 id
     */
    addToast(message, type = 'info', duration = 3000) {
      const id = ++toastSeed
      this.toasts.push({ id, message, type, duration })
      if (duration > 0) {
        window.setTimeout(() => this.removeToast(id), duration)
      }
      return id
    },
    // 按 id 移除一条提示
    removeToast(id) {
      const index = this.toasts.findIndex((item) => item.id === id)
      if (index !== -1) {
        this.toasts.splice(index, 1)
      }
    },
    // 开启一次全局加载（计数 +1）
    startLoading() {
      this.loadingCount += 1
    },
    // 结束一次全局加载（计数 -1，不小于 0）
    stopLoading() {
      this.loadingCount = Math.max(0, this.loadingCount - 1)
    },
  },
})

export default useUIStore
