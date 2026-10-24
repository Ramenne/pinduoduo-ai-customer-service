<!--
  注册页面（拼多多自动回复系统前端）
  职责（需求 2 / 规范 41，参照 xianyu-auto-reply-wangpan 注册页）：
    1. 进入时查询「是否允许注册」开关，关闭则展示「注册功能已关闭」并引导回登录；
    2. 提供「用户名 + 邮箱 + 密码 + 确认密码 + 图形验证码 + 邮箱验证码」注册表单：
       - 图形验证码输入满 4 位自动校验，通过后方可发送邮箱验证码；
       - 邮箱验证码发送带 60 秒倒计时限频；
    3. 提交调用后端 /register；成功后端不自动登录，前端跳转登录页。
  规范遵循：
    - 提示统一使用 showToast，禁止使用 alert（规范 6）；前端不二次包装错误（规范 2/4）；
    - 提交 / 关键操作展示加载遮罩 + 转圈（规范 23）；
    - 全中文文案（规范 27）；响应式适配手机端（规范 20）；
    - 不写死 localhost：请求经 @/utils/request（地址由环境变量配置，规范 21）。
-->
<script setup>
import { onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '@/api'
import { Loading } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'

const router = useRouter()

// 同一注册流程内复用的会话标识（图形验证码 / 邮箱验证码按此关联）
const sessionId = `session_${Math.random().toString(36).slice(2, 11)}_${Date.now()}`

// 是否开放注册（false 时展示「注册功能已关闭」）
const registrationEnabled = ref(false)
// 注册开关是否已查询完成（避免首屏闪烁）
const statusLoaded = ref(false)
const statusError = ref(false)

// 注册表单数据
const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
  captchaCode: '',
  verificationCode: '',
})

// 是否明文显示密码
const showPassword = ref(false)
// 提交中状态（控制加载遮罩与按钮禁用）
const submitting = ref(false)

// 图形验证码图片（data URL）
const captchaImage = ref('')
// 图形验证码是否已校验通过
const captchaVerified = ref(false)
// 图形验证码校验中
const verifying = ref(false)

// 邮箱验证码倒计时（秒）
const countdown = ref(0)
let countdownTimer = null

// 邮箱格式校验正则（与后端口径一致）
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// 查询注册开关：关闭时保留说明；失败可重试，不自动跳转。
async function loadRegistrationStatus() {
  statusLoaded.value = false
  statusError.value = false
  try {
    const data = await authApi.getRegistrationStatus()
    registrationEnabled.value = !!data?.enabled
    if (registrationEnabled.value) await loadCaptcha()
  } catch {
    statusError.value = true
  } finally {
    statusLoaded.value = true
  }
}

// 加载 / 刷新图形验证码
async function loadCaptcha() {
  try {
    const data = await authApi.generateImageCaptcha(sessionId)
    if (data && data.captcha_image) {
      captchaImage.value = data.captcha_image
      captchaVerified.value = false
      form.captchaCode = ''
    }
  } catch (e) {
    // 失败已由 request 统一提示
  }
}

// 自动校验图形验证码（输入满 4 位触发）
async function autoVerifyCaptcha() {
  if (form.captchaCode.length !== 4 || captchaVerified.value || verifying.value) {
    return
  }
  verifying.value = true
  try {
    await authApi.verifyImageCaptcha(sessionId, form.captchaCode)
    captchaVerified.value = true
    showToast('验证码验证成功', TOAST_TYPE.SUCCESS)
  } catch (e) {
    // 校验失败（错误 / 过期）已由 request 提示，刷新图形验证码重试
    captchaVerified.value = false
    loadCaptcha()
  } finally {
    verifying.value = false
  }
}

watch(() => form.captchaCode, () => {
  autoVerifyCaptcha()
})

// 启动倒计时
function startCountdown(seconds) {
  countdown.value = seconds
  countdownTimer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

// 发送邮箱验证码
async function onSendCode() {
  const username = form.username.trim()
  const email = form.email.trim()
  if (!username) {
    showToast('请先输入用户名', TOAST_TYPE.WARNING)
    return
  }
  if (!email) {
    showToast('请先输入邮箱地址', TOAST_TYPE.WARNING)
    return
  }
  if (!EMAIL_REGEX.test(email)) {
    showToast('请输入正确的邮箱格式', TOAST_TYPE.WARNING)
    return
  }
  if (!form.password || form.password.length < 6) {
    showToast('密码长度不能少于 6 位', TOAST_TYPE.WARNING)
    return
  }
  if (form.password !== form.confirmPassword) {
    showToast('两次输入的密码不一致', TOAST_TYPE.WARNING)
    return
  }
  if (!captchaVerified.value) {
    showToast('请先完成图形验证码验证', TOAST_TYPE.WARNING)
    return
  }
  if (countdown.value > 0) {
    return
  }

  try {
    await authApi.sendEmailCode(email, 'register', sessionId)
    showToast('验证码已发送到您的邮箱，请查收', TOAST_TYPE.SUCCESS)
    startCountdown(60)
  } catch (e) {
    // 发送失败（邮箱已注册 / SMTP 未配置等）已由 request 提示
  }
}

// 提交注册
async function onSubmit() {
  const username = form.username.trim()
  const email = form.email.trim()
  if (!username) {
    showToast('请输入用户名', TOAST_TYPE.WARNING)
    return
  }
  if (!email || !EMAIL_REGEX.test(email)) {
    showToast('请输入正确的邮箱地址', TOAST_TYPE.WARNING)
    return
  }
  if (!form.password || form.password.length < 6) {
    showToast('密码长度不能少于 6 位', TOAST_TYPE.WARNING)
    return
  }
  if (form.password !== form.confirmPassword) {
    showToast('两次输入的密码不一致', TOAST_TYPE.WARNING)
    return
  }
  if (!form.verificationCode.trim()) {
    showToast('请输入邮箱验证码', TOAST_TYPE.WARNING)
    return
  }

  submitting.value = true
  try {
    await authApi.register({
      username,
      email,
      password: form.password,
      verificationCode: form.verificationCode.trim(),
      sessionId,
    })
    showToast('注册成功，请登录', TOAST_TYPE.SUCCESS)
    router.replace('/login')
  } catch (e) {
    // 注册失败（验证码错误 / 用户名或邮箱已存在等）已由 request 统一提示
  } finally {
    submitting.value = false
  }
}

// 返回登录页
function goLogin() {
  router.push('/login')
}

onMounted(() => {
  loadRegistrationStatus()
})

onBeforeUnmount(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})
</script>

<template>
  <div class="register-page">
    <Loading :visible="submitting" full-screen text="注册中..." />

    <div v-if="!statusLoaded" class="register-card" role="status">正在读取注册设置…</div>
    <div v-else-if="statusError" class="register-card">
      <h1 class="register-brand__name">暂时无法读取注册设置</h1>
      <button class="register-submit" type="button" @click="loadRegistrationStatus">重试</button>
      <button class="register-link" type="button" @click="goLogin">返回登录</button>
    </div>
    <!-- 注册关闭时保留说明，由用户决定返回，不能自动跳走。 -->
    <div v-else-if="!registrationEnabled" class="register-card register-card--closed">
      <div class="register-closed__icon" aria-hidden="true">🚫</div>
      <h1 class="register-brand__name">注册功能已关闭</h1>
      <p class="register-brand__desc">管理员已关闭注册功能，如需账号请联系管理员</p>
      <button class="register-submit" type="button" @click="goLogin">返回登录</button>
    </div>

    <!-- 注册表单 -->
    <div v-else class="register-card">
      <div class="register-brand">
        <h1 class="register-brand__name">注册新账号</h1>
        <p class="register-brand__desc">创建账号后由管理员分配相应权限</p>
      </div>

      <form class="register-form" @submit.prevent="onSubmit">
        <label class="register-field">
          <span class="register-field__label">用户名</span>
          <input
            v-model="form.username"
            class="register-field__input"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
            :disabled="submitting"
          />
        </label>

        <label class="register-field">
          <span class="register-field__label">邮箱地址</span>
          <input
            v-model="form.email"
            class="register-field__input"
            type="email"
            autocomplete="email"
            placeholder="name@example.com"
            :disabled="submitting"
          />
        </label>

        <label class="register-field">
          <span class="register-field__label">密码（至少 6 位）</span>
          <div class="register-field__password">
            <input
              v-model="form.password"
              class="register-field__input"
              :type="showPassword ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="请输入密码"
              :disabled="submitting"
            />
            <button
              class="register-field__toggle"
              type="button"
              :disabled="submitting"
              @click="showPassword = !showPassword"
            >
              {{ showPassword ? '隐藏' : '显示' }}
            </button>
          </div>
        </label>

        <label class="register-field">
          <span class="register-field__label">确认密码</span>
          <input
            v-model="form.confirmPassword"
            class="register-field__input"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="new-password"
            placeholder="请再次输入密码"
            :disabled="submitting"
          />
        </label>

        <label class="register-field">
          <span class="register-field__label">图形验证码</span>
          <div class="register-field__row">
            <input
              v-model="form.captchaCode"
              class="register-field__input"
              type="text"
              maxlength="4"
              placeholder="请输入图形验证码"
              :disabled="submitting || captchaVerified"
            />
            <img
              v-if="captchaImage"
              class="register-captcha__img"
              :src="captchaImage"
              alt="图形验证码"
              title="点击更换验证码"
              @click="loadCaptcha"
            />
          </div>
          <span
            class="register-field__hint"
            :class="{ 'register-field__hint--ok': captchaVerified }"
          >
            {{ captchaVerified ? '✓ 验证成功' : verifying ? '验证中...' : '点击图片可更换验证码' }}
          </span>
        </label>

        <label class="register-field">
          <span class="register-field__label">邮箱验证码</span>
          <div class="register-field__row">
            <input
              v-model="form.verificationCode"
              class="register-field__input"
              type="text"
              maxlength="6"
              placeholder="请输入 6 位邮箱验证码"
              :disabled="submitting"
            />
            <button
              class="register-code-btn"
              type="button"
              :disabled="submitting || !captchaVerified || countdown > 0"
              @click="onSendCode"
            >
              {{ countdown > 0 ? `${countdown}s 后重发` : '发送验证码' }}
            </button>
          </div>
        </label>

        <button class="register-submit" type="submit" :disabled="submitting">
          {{ submitting ? '注册中...' : '注册' }}
        </button>

        <button class="register-link" type="button" @click="goLogin">
          已有账号？返回登录
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.register-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: linear-gradient(135deg, var(--color-primary-light) 0%, var(--color-bg) 100%);
}
.register-card {
  width: 100%;
  max-width: 400px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 14px;
  box-shadow: var(--shadow-card);
  padding: 32px 28px;
}
.register-card--closed {
  text-align: center;
}
.register-closed__icon {
  font-size: 40px;
  margin-bottom: 12px;
}
.register-brand {
  text-align: center;
  margin-bottom: 24px;
}
.register-brand__name {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
}
.register-brand__desc {
  margin-top: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.register-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.register-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.register-field__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.register-field__row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.register-field__password {
  position: relative;
  display: flex;
  width: 100%;
}
.register-field__password .register-field__input {
  padding-right: 52px;
}
.register-field__input {
  box-sizing: border-box;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s ease;
}
/* 行内排列时输入框横向撑满剩余空间，高度仍由上面的 40px 决定 */
.register-field__row .register-field__input {
  flex: 1 1 auto;
  min-width: 0;
}
.register-field__input:focus {
  border-color: var(--color-primary);
}
.register-field__input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.register-field__toggle {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 6px;
}
.register-field__toggle:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.register-field__hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.register-field__hint--ok {
  color: var(--color-success, #16a34a);
}
.register-captcha__img {
  box-sizing: border-box;
  display: block;
  height: 40px;
  min-height: 40px;
  max-height: 40px;
  width: 120px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  cursor: pointer;
  object-fit: cover;
  flex-shrink: 0;
}
.register-code-btn {
  box-sizing: border-box;
  flex-shrink: 0;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--color-primary);
  border-radius: 8px;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.register-code-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.register-submit {
  box-sizing: border-box;
  margin-top: 8px;
  height: 42px;
  border: none;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}
.register-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}
.register-submit:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}
.register-link {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 0;
}
@media (max-width: 480px) {
  .register-card {
    padding: 24px 18px;
  }
}
</style>
