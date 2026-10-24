// 认证与个人设置接口模块（拼多多自动回复系统前端）
// 职责：
//   1. 集中封装「登录 / 登出」与「个人设置（账户信息 / 修改密码 / 联系方式）」相关后端接口；
//   2. 统一经 @/utils/request 发起请求（超时 90s、统一响应体解析、错误经 showToast 提示，
//      后端地址经环境变量配置，禁止写死 localhost）；
//   3. 维护登录令牌与当前用户信息在 localStorage 的读写键名，供登录页与请求封装共用。
// 说明：request 封装在业务成功时直接 resolve 后端 data，失败时已统一弹窗提示并以「已处理」
//       的拒绝结束；故调用方仅需 await 成功分支，失败分支无需再次提示。

import { get, post, put } from '@/utils/request'

// 登录令牌在 localStorage 中的键名（须与 utils/request.js 的 TOKEN_STORAGE_KEY 保持一致）
export const TOKEN_STORAGE_KEY = 'auth_token'
// 当前登录用户信息在 localStorage 中的键名（用于刷新页面后恢复用户状态）
export const USER_STORAGE_KEY = 'auth_user'

/**
 * 账号密码登录（需求 1.1/1.2）
 * @param {string} username 用户名
 * @param {string} password 明文密码
 * @param {string} [captchaTicket] 滑块验证通过后签发的一次性票据（开启登录验证码时必填）
 * @returns {Promise<{token: string, user: object}>} 成功时返回令牌与脱敏用户信息
 */
export function login(username, password, captchaTicket) {
  return post('/login', { username, password, captcha_ticket: captchaTicket || null })
}

/**
 * 账号密码注册（邮箱验证码注册，自动分配默认角色）
 * 参照 xianyu-auto-reply-wangpan 注册流程：用户名 + 邮箱 + 密码 + 邮箱验证码。
 * 注册成功后端不自动登录，前端据 success 跳转登录页。
 * @param {{username: string, email: string, password: string, verificationCode: string, sessionId?: string}} payload 注册信息
 * @returns {Promise<null>} 成功 resolve（无令牌）；失败已由 request 统一提示
 */
export function register(payload) {
  return post('/register', {
    username: payload.username,
    email: payload.email,
    password: payload.password,
    verification_code: payload.verificationCode,
    session_id: payload.sessionId || null,
  })
}

/**
 * 查询「是否允许用户注册」开关（公开接口，注册页登录前调用）
 * @returns {Promise<{enabled: boolean}>} 是否开放注册
 */
export function getRegistrationStatus() {
  return get('/register/status')
}

/**
 * 生成图形字符验证码（公开接口，注册页人机校验）
 * @param {string} sessionId 前端会话标识
 * @returns {Promise<{captcha_image: string, session_id: string}>} 图片 data URL
 */
export function generateImageCaptcha(sessionId) {
  return post('/captcha/generate', { session_id: sessionId })
}

/**
 * 校验图形字符验证码（公开接口）
 * @param {string} sessionId 会话标识
 * @param {string} captchaCode 用户输入的验证码
 * @returns {Promise<null>} 通过 resolve；失败已由 request 统一提示
 */
export function verifyImageCaptcha(sessionId, captchaCode) {
  return post('/captcha/verify', { session_id: sessionId, captcha_code: captchaCode })
}

/**
 * 发送邮箱验证码（公开接口，注册页校验邮箱归属）
 * @param {string} email 收件邮箱
 * @param {string} [type] 场景：register/login/reset，默认 register
 * @param {string} [sessionId] 前端会话标识
 * @returns {Promise<null>} 发送成功 resolve；失败已由 request 统一提示
 */
export function sendEmailCode(email, type = 'register', sessionId) {
  return post('/captcha/send-email-code', { email, type, session_id: sessionId || null })
}

/**
 * 登出：使当前登录令牌失效（需求 1.5）
 * @returns {Promise<null>}
 */
export function logout() {
  return post('/logout')
}

/**
 * 查询当前用户账户信息（需求 22.1）
 * @returns {Promise<object>} 脱敏后的用户信息（用户名、角色等只读展示）
 */
export function getProfile() {
  return get('/profile')
}

/**
 * 修改当前用户密码（需求 22.2/22.3/22.5）
 * 注意：新密码长度与两次一致性由前端先行校验（需求 22.4），此处仅在校验通过后调用。
 * @param {string} currentPassword 当前密码（明文）
 * @param {string} newPassword 新密码（明文）
 * @returns {Promise<null>}
 */
export function changePassword(currentPassword, newPassword) {
  return put('/profile/password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

/**
 * 保存个人联系方式（微信、QQ）（需求 22.6/22.7）
 * @param {{wechat?: string, qq?: string}} contact 联系方式（省略的字段表示不修改）
 * @returns {Promise<object>} 更新后脱敏的用户信息
 */
export function updateContact(contact) {
  return put('/profile/contact', contact)
}

/**
 * 读取登录页品牌信息（需求 21.11）
 * 说明：品牌接口当前为管理员专属（登录前无鉴权），此处为「尽力而为」读取——
 *       读取失败由 request 静默处理（已处理拒绝），登录页回退为默认中文文案。
 * @returns {Promise<object>} 品牌信息（system_name / title / description）
 */
export function getBrand() {
  return get('/settings/brand')
}

/**
 * 查询「是否启用登录验证码」开关（公开接口，登录前调用）
 * @returns {Promise<{enabled: boolean}>} 是否需要在登录页展示滑块验证
 */
export function getCaptchaStatus() {
  return get('/captcha/status')
}

/**
 * 生成滑块拼图验证码挑战（公开接口）
 * @returns {Promise<object>} {challenge_id, bg_image, puzzle_image, y, piece_size, bg_width, bg_height}
 */
export function generateSliderCaptcha() {
  return post('/captcha/slider/generate')
}

/**
 * 校验滑块拖动位移（公开接口）
 * @param {string} challengeId 挑战标识
 * @param {number} distance 拖动横向位移（像素，相对背景图实际宽度）
 * @returns {Promise<{ticket: string}>} 通过返回一次性票据，登录时回传
 */
export function verifySliderCaptcha(challengeId, distance) {
  return post('/captcha/slider/verify', { challenge_id: challengeId, distance })
}

export default {
  login,
  register,
  getRegistrationStatus,
  generateImageCaptcha,
  verifyImageCaptcha,
  sendEmailCode,
  logout,
  getProfile,
  changePassword,
  updateContact,
  getBrand,
  getCaptchaStatus,
  generateSliderCaptcha,
  verifySliderCaptcha,
}
