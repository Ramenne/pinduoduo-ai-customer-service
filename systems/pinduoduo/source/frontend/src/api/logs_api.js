// 日志查询接口模块（对应 backend /message-logs、/risk-logs、/system-logs 路由，需求 19 / 21.4）
// 职责：封装消息日志、风控日志、系统日志的后端分页查询调用，统一经 @/utils/request 发起。
// 说明：
//   1. 一律采用后端分页（规范 28），支持按店铺与时间范围筛选（需求 19.3）；
//   2. 后端仅提供查询接口，不提供删除接口（禁止删除日志数据，需求 19.5 / 规范 11）；
//   3. 时间范围按北京时间口径（需求 24.8），格式 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM:SS"；
//   4. 后端地址经环境变量配置（禁止写死 localhost，规范 21 / 需求 25.4）。
import { get } from '@/utils/request'


// 分页查询消息日志（需求 19.1 / 19.3）
// 参数：{ shop_pk?, start_time?, end_time?, page, page_size }
// 返回：{ list, total, page, page_size }
export function fetchMessageLogs(params = {}) {
  return get(`/message-logs`, params)
}

// 分页查询风控日志（需求 19.2 / 19.3）
// 参数：{ shop_pk?, risk_type?, start_time?, end_time?, page, page_size }
export function fetchRiskLogs(params = {}) {
  return get(`/risk-logs`, params)
}

// 分页查询系统日志（需求 21.4）
// 参数：{ level?, module?, start_time?, end_time?, page, page_size }
export function fetchSystemLogs(params = {}) {
  return get(`/system-logs`, params)
}
