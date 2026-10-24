// 通用格式化工具（拼多多自动回复系统前端）
// 职责：提供前端展示用的格式化纯函数，目前包含时间格式化。
// 说明：后端时间字段统一为北京时间（规范 17），前端仅做展示格式化，不做时区换算，
//       避免二次偏移导致显示错误。

// 将后端返回的时间值格式化为「YYYY-MM-DD HH:mm:ss」字符串（始终北京时间，规范 17）。
// 入参可为：
//   1. 不带时区的北京时间字符串（后端约定，如 "2026-06-10T12:00:00" / "2026-06-10 12:00:00"）：
//      其本身就是北京时间墙钟值，直接抽取年月日时分秒重排，不经浏览器本地时区解释，
//      避免在非北京时区浏览器上出现 ±N 小时偏移。
//   2. 带时区标识的时刻（含 Z 或 ±HH:MM 偏移）或时间戳：是一个确定时刻，
//      统一换算到北京时间（UTC+8）后展示。
// 为空 / 非法时返回占位「-」。
export function formatDateTime(value) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  const pad = (num) => String(num).padStart(2, '0')
  const text = String(value).trim()

  // 是否带时区信息：以 Z 结尾，或以 ±HH:MM / ±HHMM 偏移结尾。
  const hasTimezone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(text)

  if (typeof value === 'string' && !hasTimezone) {
    // 不带时区：按北京时间墙钟直接抽取分量，不做时区换算（避免二次偏移）。
    const m = text.match(
      /(\d{4})\D(\d{1,2})\D(\d{1,2})(?:[ T](\d{1,2})\D(\d{1,2})(?:\D(\d{1,2}))?)?/
    )
    if (!m) {
      return text
    }
    const [, y, mo, d, h = '0', mi = '0', s = '0'] = m
    return `${y}-${pad(mo)}-${pad(d)} ${pad(h)}:${pad(mi)}:${pad(s)}`
  }

  // 带时区的时刻 / 时间戳：换算到北京时间（UTC+8）。
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    // 非法时间值原样返回字符串形式，避免显示 Invalid Date
    return text
  }
  // 取该时刻的 UTC 毫秒 + 8 小时偏移，再以 UTC 字段读取，得到北京时间墙钟。
  const beijing = new Date(date.getTime() + 8 * 60 * 60 * 1000)
  const year = beijing.getUTCFullYear()
  const month = pad(beijing.getUTCMonth() + 1)
  const day = pad(beijing.getUTCDate())
  const hour = pad(beijing.getUTCHours())
  const minute = pad(beijing.getUTCMinutes())
  const second = pad(beijing.getUTCSeconds())
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`
}

// 将数值格式化为千分位分隔的字符串（用于仪表盘等指标展示）。
// 入参为空 / 非数值时回退为「0」，避免展示 undefined / NaN。
export function formatNumber(value) {
  const num = Number(value)
  if (value === null || value === undefined || value === '' || Number.isNaN(num)) {
    return '0'
  }
  // 使用 zh-CN 本地化千分位分隔（如 1234567 -> 1,234,567）
  return num.toLocaleString('zh-CN')
}
