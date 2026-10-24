// 主题工具模块：拼多多自动回复系统前端（任务 16.2）
// 职责：
//   1. 管理「暗黑模式」开关：在 <html> 上添加/移除 .dark 类并持久化到 localStorage（规范 25）；
//   2. 管理「主题色」预设：在 <html> 上设置 data-theme-color 属性并持久化（规范 26）；
//   3. 提供初始化方法，供应用启动时与 index.html 提前注入的暗黑判断保持一致，避免首屏闪烁。
// 说明：颜色取值统一由 styles/theme.css 经 CSS 变量定义，本模块仅切换模式/预设，不直接写颜色值。

// localStorage 存储键（与 index.html 中提前执行的脚本保持一致）
const THEME_MODE_KEY = 'theme' // 取值：'light' | 'dark'
const THEME_COLOR_KEY = 'theme_color' // 取值见 THEME_COLOR_PRESETS 的 key

// 可选主题色预设（中文文案用于设置界面展示，规范 27）
export const THEME_COLOR_PRESETS = [
  { key: 'blue', label: '海洋蓝', color: '#1677ff' },
  { key: 'green', label: '翡翠绿', color: '#15a97c' },
  { key: 'purple', label: '雾感紫', color: '#722ed1' },
  { key: 'orange', label: '落日橙', color: '#fa6400' },
  { key: 'red', label: '玫瑰红', color: '#e53935' },
]

// 默认主题色预设 key
const DEFAULT_THEME_COLOR = 'blue'

// 判断某个主题色 key 是否合法
function isValidThemeColor(key) {
  return THEME_COLOR_PRESETS.some((preset) => preset.key === key)
}

// 读取系统偏好是否为暗色
function prefersDark() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
}

/**
 * 获取当前暗黑模式状态（以 <html> 上的 .dark 类为准）
 * @returns {boolean} 是否为暗黑模式
 */
export function isDarkMode() {
  if (typeof document === 'undefined') {
    return false
  }
  return document.documentElement.classList.contains('dark')
}

/**
 * 应用暗黑模式开关：切换 .dark 类并持久化
 * @param {boolean} dark 是否启用暗黑模式
 * @returns {boolean} 应用后的暗黑模式状态
 */
export function applyDarkMode(dark) {
  if (typeof document === 'undefined') {
    return dark
  }
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(THEME_MODE_KEY, dark ? 'dark' : 'light')
  return dark
}

/**
 * 切换暗黑模式（亮 <-> 暗）
 * @returns {boolean} 切换后的暗黑模式状态
 */
export function toggleDarkMode() {
  return applyDarkMode(!isDarkMode())
}

/**
 * 读取已保存的主题色预设 key（无效或未设置时回退默认）
 * @returns {string} 主题色预设 key
 */
export function getStoredThemeColor() {
  if (typeof window === 'undefined') {
    return DEFAULT_THEME_COLOR
  }
  const stored = localStorage.getItem(THEME_COLOR_KEY)
  return isValidThemeColor(stored) ? stored : DEFAULT_THEME_COLOR
}

/**
 * 应用主题色预设：在 <html> 设置 data-theme-color 并持久化（规范 26）
 * @param {string} colorKey 主题色预设 key
 * @returns {string} 应用后的主题色 key
 */
export function applyThemeColor(colorKey) {
  const key = isValidThemeColor(colorKey) ? colorKey : DEFAULT_THEME_COLOR
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme-color', key)
    localStorage.setItem(THEME_COLOR_KEY, key)
  }
  return key
}

/**
 * 初始化主题（应用启动时调用）：
 *   - 暗黑模式：以已保存值优先，否则跟随系统偏好；
 *   - 主题色：应用已保存的预设（默认蓝）。
 * @returns {{ dark: boolean, color: string }} 初始化后的主题状态
 */
export function initializeTheme() {
  const storedMode = typeof window !== 'undefined' ? localStorage.getItem(THEME_MODE_KEY) : null
  const dark = storedMode === 'dark' || (!storedMode && prefersDark())
  applyDarkMode(dark)
  const color = applyThemeColor(getStoredThemeColor())
  return { dark, color }
}
