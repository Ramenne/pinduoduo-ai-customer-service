// XSS 安全渲染工具（拼多多自动回复系统前端）
// 职责：
//   1. 提供纯函数 escapeHtml：对用户输入中的 HTML 特殊字符进行转义，避免被浏览器当作
//      可执行脚本/标记解析（规范 22 / 需求 23.10）；
//   2. 提供 sanitizeHtml：基于白名单对一段 HTML 进行清洗，去除脚本、事件属性与危险协议，
//      供需要富文本展示的场景（配合 v-html）安全渲染；
//   3. escapeHtml 为不依赖 DOM 的纯字符串函数，便于属性测试（对应 Property 24，测试见任务 16.4）。
// 说明：
//   - 默认优先使用 escapeHtml 进行纯文本转义；仅在确需保留部分标签的富文本场景使用 sanitizeHtml。
//   - escapeHtml 设计为「不重复转义已有实体」，因此对已转义文本再次转义不改变其渲染语义（幂等）。

// 允许保留的标签（富文本清洗白名单）
const ALLOWED_TAGS = new Set(['a', 'b', 'br', 'div', 'em', 'i', 'p', 'small', 'span', 'strong', 'u'])

// 需要连同内容一并删除的危险标签
const STRIP_CONTENT_TAGS = new Set([
  'button', 'embed', 'form', 'iframe', 'input', 'object', 'script', 'select', 'style', 'textarea',
])

// 允许的链接协议（其余协议如 javascript: 一律剔除）
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:'])

// 各标签允许保留的属性白名单
const ALLOWED_ATTRIBUTES = {
  a: new Set(['href', 'target', 'title']),
}

/**
 * 对纯文本进行 HTML 转义（纯函数，不依赖 DOM）。
 * 将 & < > " ' 转义为对应实体，转义后输出不再包含可被解析为标记的原始字符。
 * 采用负向先行断言跳过「已是合法实体」的 &，从而对已转义文本再次转义保持语义不变（幂等）。
 *
 * @param {*} input 任意输入（非字符串将被转为字符串；null/undefined 视为空串）
 * @returns {string} 转义后的安全文本
 */
export function escapeHtml(input) {
  // null / undefined 统一按空串处理，避免渲染出 "null"/"undefined"
  if (input === null || input === undefined) {
    return ''
  }
  const text = String(input)
  return text
    // 仅转义「不属于已有实体」的 &，保证幂等（&amp; / &#39; / &#x3c; 等不被二次转义）
    .replace(/&(?![a-zA-Z][a-zA-Z0-9]*;|#\d+;|#x[0-9a-fA-F]+;)/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 规范化链接 target，仅允许 _blank / _self
function normalizeTarget(target) {
  const normalized = String(target || '').trim().toLowerCase()
  if (normalized === '_blank' || normalized === '_self') {
    return normalized
  }
  return null
}

// 校验并返回安全的 href；非法协议（如 javascript:）返回 null
function sanitizeHref(href) {
  const trimmedHref = String(href || '').trim()
  if (!trimmedHref) {
    return null
  }
  // 站内相对路径与锚点直接放行
  if (trimmedHref.startsWith('/') || trimmedHref.startsWith('#')) {
    return trimmedHref
  }
  // 浏览器环境下用 URL 解析协议；非浏览器环境无法解析则一律剔除
  if (typeof window === 'undefined' || typeof URL === 'undefined') {
    return null
  }
  try {
    const parsed = new URL(trimmedHref, window.location.origin)
    if (ALLOWED_PROTOCOLS.has(parsed.protocol)) {
      return trimmedHref
    }
  } catch {
    return null
  }
  return null
}

// 递归清洗单个 DOM 节点（就地修改）
function sanitizeNode(node) {
  // 文本节点安全，保留
  if (node.nodeType === Node.TEXT_NODE) {
    return
  }
  // 非元素节点（注释等）一律移除
  if (node.nodeType !== Node.ELEMENT_NODE) {
    node.remove()
    return
  }

  const element = node
  const tagName = element.tagName.toLowerCase()

  // 不在白名单中的标签处理
  if (!ALLOWED_TAGS.has(tagName)) {
    // 危险标签连同内容一并删除
    if (STRIP_CONTENT_TAGS.has(tagName)) {
      element.remove()
      return
    }
    // 其余未知标签：剥离标签本身但保留其子内容（提升到父节点）
    const parent = element.parentNode
    if (!parent) {
      element.remove()
      return
    }
    const childNodes = Array.from(element.childNodes)
    for (const childNode of childNodes) {
      parent.insertBefore(childNode, element)
      sanitizeNode(childNode)
    }
    element.remove()
    return
  }

  // 清洗属性：移除事件属性（on*）与非白名单属性
  const allowedAttributes = ALLOWED_ATTRIBUTES[tagName] || new Set()
  for (const attribute of Array.from(element.attributes)) {
    const attributeName = attribute.name.toLowerCase()
    if (attributeName.startsWith('on') || !allowedAttributes.has(attributeName)) {
      element.removeAttribute(attribute.name)
      continue
    }
    if (tagName === 'a' && attributeName === 'href') {
      const safeHref = sanitizeHref(attribute.value)
      if (safeHref) {
        element.setAttribute('href', safeHref)
      } else {
        element.removeAttribute(attribute.name)
      }
      continue
    }
    if (tagName === 'a' && attributeName === 'target') {
      const safeTarget = normalizeTarget(attribute.value)
      if (safeTarget) {
        element.setAttribute('target', safeTarget)
      } else {
        element.removeAttribute(attribute.name)
      }
    }
  }

  // 新窗口打开的链接补充 rel，避免反向标签钓鱼
  if (tagName === 'a') {
    if (element.getAttribute('target') === '_blank') {
      element.setAttribute('rel', 'noopener noreferrer')
    } else {
      element.removeAttribute('rel')
    }
  }

  // 递归清洗子节点
  for (const childNode of Array.from(element.childNodes)) {
    sanitizeNode(childNode)
  }
}

/**
 * 基于白名单清洗一段 HTML，去除脚本、事件属性与危险协议后返回安全 HTML 字符串。
 * 仅用于确需保留部分标签的富文本展示场景（配合 v-html）。
 * 非浏览器环境（无 document）无法解析时，退化为整体转义，确保不残留可执行标记。
 *
 * @param {string} html 原始 HTML 字符串
 * @returns {string} 清洗后的安全 HTML
 */
export function sanitizeHtml(html) {
  const raw = String(html || '')
  if (!raw.trim()) {
    return ''
  }
  // 非浏览器环境无法做 DOM 清洗，退化为整体转义（绝不残留可执行标记）
  if (typeof document === 'undefined') {
    return escapeHtml(raw)
  }
  const template = document.createElement('template')
  template.innerHTML = raw
  for (const childNode of Array.from(template.content.childNodes)) {
    sanitizeNode(childNode)
  }
  return template.innerHTML
}

export default escapeHtml
