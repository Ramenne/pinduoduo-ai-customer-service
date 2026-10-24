// 轻量消息提示工具（占位实现）
// 说明：
//   1. 本文件为「任务 16.3 通用 Toast 组件」尚未实现前的占位/轻量实现，提供统一的 showToast 接口供
//      请求封装（request.js）等模块调用，避免使用浏览器原生 alert（规范 6）。
//   2. 任务 16.3 实现正式 Toast 组件后，可通过 registerToastHandler 注入正式实现，
//      本文件其余调用点无需改动（预留接口，依赖说明见此注释）。
//   3. 提示信息以页面浮层方式呈现，所有错误对用户可见，浏览器控制台不产生未处理报错（规范 4）。

// 提示类型枚举（中文文案由调用方传入；类型仅用于样式区分）
export const TOAST_TYPE = {
  SUCCESS: 'success',
  ERROR: 'error',
  WARNING: 'warning',
  INFO: 'info',
}

// 正式 Toast 处理器（任务 16.3 注入）；未注入时使用本文件内置的轻量 DOM 实现
let externalToastHandler = null

/**
 * 注册正式 Toast 处理器（供任务 16.3 的通用组件接入）
 * @param {(message: string, type: string, duration: number) => void} handler 处理函数
 */
export function registerToastHandler(handler) {
  externalToastHandler = typeof handler === 'function' ? handler : null
}

// 内置轻量提示容器（懒创建），仅在未注入正式处理器时使用
let placeholderContainer = null

// 获取或创建内置提示容器
function ensurePlaceholderContainer() {
  if (placeholderContainer && document.body.contains(placeholderContainer)) {
    return placeholderContainer
  }
  const container = document.createElement('div')
  // 固定在顶部居中，置于最上层，不阻挡页面交互
  container.style.cssText = [
    'position:fixed',
    'top:24px',
    'left:50%',
    'transform:translateX(-50%)',
    'z-index:100010',
    'display:flex',
    'flex-direction:column',
    'align-items:center',
    'gap:8px',
    'pointer-events:none',
  ].join(';')
  document.body.appendChild(container)
  placeholderContainer = container
  return container
}

// 不同类型对应的背景色（保证文字与背景对比清晰，规范 24）
const TYPE_BACKGROUND = {
  [TOAST_TYPE.SUCCESS]: '#16a34a',
  [TOAST_TYPE.ERROR]: '#dc2626',
  [TOAST_TYPE.WARNING]: '#d97706',
  [TOAST_TYPE.INFO]: '#2563eb',
}

// 内置轻量提示实现：在页面顶部展示一条可自动消失的提示
function showPlaceholderToast(message, type, duration) {
  const container = ensurePlaceholderContainer()
  const item = document.createElement('div')
  item.textContent = message
  item.style.cssText = [
    `background:${TYPE_BACKGROUND[type] || TYPE_BACKGROUND[TOAST_TYPE.INFO]}`,
    'color:#ffffff',
    'padding:10px 16px',
    'border-radius:6px',
    'font-size:14px',
    'line-height:1.5',
    'max-width:80vw',
    'word-break:break-all',
    'box-shadow:0 4px 12px rgba(0,0,0,0.15)',
    'pointer-events:auto',
  ].join(';')
  container.appendChild(item)

  // 到时自动移除提示
  window.setTimeout(() => {
    if (item.parentNode === container) {
      container.removeChild(item)
    }
  }, duration)
}

/**
 * 统一消息提示入口（替代 alert，规范 6）
 * @param {string} message 中文提示文案（一般为后端返回的 message）
 * @param {string} [type] 提示类型，取值见 TOAST_TYPE，默认 info
 * @param {number} [duration] 自动关闭毫秒数，默认 3000
 */
export function showToast(message, type = TOAST_TYPE.INFO, duration = 3000) {
  // 空消息不展示，避免出现空白提示
  if (!message) {
    return
  }
  // 已注入正式处理器时优先使用（任务 16.3）
  if (externalToastHandler) {
    externalToastHandler(message, type, duration)
    return
  }
  // 否则使用内置轻量实现（占位）
  showPlaceholderToast(message, type, duration)
}

export default showToast
