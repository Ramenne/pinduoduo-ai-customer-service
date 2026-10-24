// 通知接口模块（对应 backend /notify 路由，需求 18）
// 职责：封装通知渠道配置、测试发送、系统事件推送、通知记录查询与渠道类型字典等接口调用，
//       统一经 @/utils/request 发起；后端分页（规范 28）。
// 说明：后端地址经环境变量配置（禁止写死 localhost，规范 21 / 需求 25.4）。
import { get, post, put } from '@/utils/request'


// 分页查询通知渠道列表（需求 18.5 配套）
// 参数：{ page, page_size, enabled?, shop_pk? }
export function fetchNotifyChannels(params = {}) {
  return get(`/notify/channels`, params)
}

// 创建通知渠道（需求 18.1，店铺级）
// 参数：{ shop_pk, channel_type, target, enabled }
export function createNotifyChannel(payload) {
  return post(`/notify/channels`, payload)
}

// 修改通知渠道（需求 18.1 配套）
// 参数：channelId、{ channel_type?, target?, enabled? }
export function updateNotifyChannel(channelId, payload) {
  return put(`/notify/channels/${channelId}`, payload)
}

// 对某通知渠道发起测试发送（需求 18.2）
// 参数：channelId、{ content? }
export function testNotifyChannel(channelId, payload = {}) {
  return post(`/notify/channels/${channelId}/test`, payload)
}

// 分页查询通知记录（需求 18.5）
// 参数：{ page, page_size, channel_id?, event_type?, send_result? }
export function fetchNotifyRecords(params = {}) {
  return get(`/notify/records`, params)
}

// 查询通知渠道类型枚举字典（中文文案，需求 18.x / 24.7）
// 返回：[{ key, label }, ...]
export function fetchChannelTypes() {
  return get(`/notify/channel-types`)
}
