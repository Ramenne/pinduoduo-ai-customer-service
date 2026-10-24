// 风控接口模块（对应 backend /risk-types 等路由，需求 13）
// 职责：封装风控类型枚举字典查询，供风控日志「按风控类型筛选」下拉复用。
// 说明：完整风控规则配置见任务 17.4；后端地址经环境变量配置（禁止写死 localhost，规范 21）。
import { get } from '@/utils/request'


// 查询风控类型枚举字典（中文文案，需求 13.4 / 24.7）
// 返回：[{ key, label }, ...]
export function fetchRiskTypes() {
  return get(`/risk-types`)
}
