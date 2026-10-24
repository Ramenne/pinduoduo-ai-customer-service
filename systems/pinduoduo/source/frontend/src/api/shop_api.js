// 店铺接口模块（对应 backend /shops 路由，需求 3 / 4）
// 职责：封装店铺与账号管理相关接口调用，供店铺管理页面（任务 17.2）与各页面
//       「按店铺筛选」下拉（任务 17.3/17.4/17.5）复用，统一经 @/utils/request 发起。
// 说明：后端地址经环境变量配置（禁止写死 localhost，规范 21 / 需求 25.4）；
//       成功时由 request 拦截器直接 resolve 后端 data，失败已统一 showToast 提示。
import { del, get, post, put } from '@/utils/request'

// 分页查询店铺列表（北京时间倒序、后端分页、数据范围隔离，需求 3.3 / 3.7）
// 参数：{ page, page_size, status? }
// 返回：{ list, total, page, page_size }，list 项含 { id, shop_id, shop_name, ... }
export function fetchShops(params = {}) {
  return get('/shops', params)
}

// 拉取用于「按店铺筛选」下拉的店铺选项（取较大页一次性加载，返回 [{ value:id, label:shop_name }]）
// 说明：筛选下拉一般店铺数量有限，采用单页较大 page_size 获取；如店铺极多可后续改为远程搜索。
export async function fetchShopOptions() {
  const data = await fetchShops({ page: 1, page_size: 100 })
  const list = (data && data.list) || []
  return list.map((shop) => ({
    value: shop.id,
    label: shop.shop_name || `店铺 #${shop.id}`,
  }))
}

// 查询单个店铺详情（含反显账号 / 密码 / Cookie 明文，供编辑回显，需求 3.6）
export function fetchShopDetail(shopPk) {
  return get(`/shops/${shopPk}`)
}

// 新增 / 更新店铺（upsert 幂等，需求 3.1 / 3.2）
// payload: { shop_id, shop_name?, shop_logo?, channel_id?, remark?, cookies?, username?, password? }
export function upsertShop(payload) {
  return post('/shops', payload)
}

// 修改店铺备注 / 启用状态 / 关联配置 / 账号凭据（需求 3.4 / 3.6）
// payload: { remark?, shop_name?, shop_logo?, channel_id?, enabled?, username?, cookies?, password? }
export function updateShop(shopPk, payload) {
  return put(`/shops/${shopPk}`, payload)
}

// 停用店铺并断开连接（逻辑删除，需求 3.5）
export function disableShop(shopPk) {
  return put(`/shops/${shopPk}/disable`)
}

// 通过「手动粘贴 Cookie 导入」新增店铺（需求 4.3 / 4.4）
// 后端经 websocket 服务校验 Cookie 有效性并自动获取真实店铺信息后落库，无需手填 shop_id。
// payload: { cookies, remark? }
export function importShopByCookie(payload) {
  return post('/shops/import-by-cookie', payload)
}

// 通过「账号密码登录」新增店铺（需求 4.1 / 4.2）
// 后端经 websocket 服务的 Playwright 登录拼多多并自动获取真实店铺信息后落库，无需手填 shop_id。
// payload: { username, password, remark? }
export function loginShopByPassword(payload) {
  // 登录服务最多等待 180 秒，前端应留出响应传输余量。
  return post('/shops/login-by-password', payload, { timeout: 195000 })
}

// -------------------- 扫码登录（本轮新增） --------------------
// 说明：浏览器自动化在 websocket 服务内以「一次性独立上下文」执行，backend 只做
//       权限、归属与身份一致性校验。凭据只在服务间内网流转并立即加密入库，
//       任何响应都不会把 Cookie 交给前端。

// 创建扫码会话（新增店铺，或为 target_shop_id 指定的已有店铺重新授权）
// payload: { target_shop_id? }
// 返回：{ session_id, state, qr_image_base64, qr_rotation, expires_at, ... }
export function createQrSession(payload = {}) {
  return post('/qr-login/sessions', payload)
}

// 轮询扫码会话状态（返回体不含任何凭据）。
// 失败类终态由后端以可区分业务码返回：42604 需人工验证 / 42605 已过期 / 42602 失败。
export function getQrSession(sessionId) {
  return get(`/qr-login/sessions/${sessionId}`)
}

export function getQrWindow(sessionId) {
  return get(`/qr-login/sessions/${sessionId}/frame`)
}

export function sendQrInput(sessionId, payload) {
  return post(`/qr-login/sessions/${sessionId}/input`, payload)
}

// 绑定扫码结果：仅在身份核验通过且落库成功后才 resolve，前端据此才显示「绑定成功」。
// 店铺不一致时后端返回 42606 且不改动原凭据；重复调用幂等。
export function bindQrSession(sessionId) {
  return post(`/qr-login/sessions/${sessionId}/bind`, {})
}

// 取消扫码会话并释放其浏览器资源与临时二维码。
// silentOnFail：轮询收尾 / 组件卸载时调用，失败不弹全局提示（用户已离开该弹窗）。
export async function cancelQrSession(sessionId, { silentOnFail = false } = {}) {
  if (silentOnFail) {
    try {
      return await del(`/qr-login/sessions/${sessionId}`)
    } catch (e) {
      return null
    }
  }
  return del(`/qr-login/sessions/${sessionId}`)
}

export const fetchShopOverview = (params = {}) => get('/chat/workspace/shop-overview', params)


// 账号密码登录也使用短请求创建会话，进度、人工操作、取消与保存复用会话接口。
export function createPasswordSession(payload) {
  return post('/qr-login/sessions', { ...payload, login_mode: 'password' })
}


export function submitLoginVerification(sessionId, payload) {
  return post(`/qr-login/sessions/${sessionId}/verification`, payload)
}
