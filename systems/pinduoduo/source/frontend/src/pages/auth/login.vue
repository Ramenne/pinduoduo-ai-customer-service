<!--
  登录页面（拼多多自动回复系统前端，任务 17.1）
  职责（需求 1.1/1.2/21.11）：
    1. 提供账号密码登录表单，提交后调用后端 /login 接口；
    2. 登录成功写入令牌与用户信息（user_store + localStorage），按 redirect 参数回跳，
       默认进入首页；
    3. 展示登录页品牌信息（系统名称、标题、描述）——尽力而为读取后端 /settings/brand，
       读取失败回退默认中文文案（品牌接口当前为管理员专属，登录前无鉴权）。
  规范遵循：
    - 提示统一使用 showToast，禁止使用 alert（规范 6）；
    - 提交过程展示加载遮罩 + 转圈（规范 23）；
    - 全中文文案（规范 27）；响应式适配手机端（规范 20）；
    - 不写死 localhost：所有请求经 @/utils/request（地址由环境变量配置，规范 21）；
    - 品牌文案经文本插值渲染，不使用 v-html，避免 XSS（规范 22）。
-->
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '@/api'
import { useUserStore } from '@/store'
import { Loading, SliderCaptcha } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 默认品牌文案（接口不可用时回退展示，全中文）
const DEFAULT_BRAND = {
  system_name: 'OIN',
  title: '欢迎登录',
  description: '平台账号与客服管理',
}

// 登录页品牌信息（响应式，初始为默认值）
const brand = reactive({ ...DEFAULT_BRAND })

// 登录表单数据
const form = reactive({
  username: '',
  password: '',
})

// 提交中状态（控制加载遮罩与按钮禁用）
const submitting = ref(false)

// 是否启用登录滑块验证码（由系统设置控制，登录前公开查询；null 表示尚未确定）
const captchaEnabled = ref(false)
// 滑块验证通过后获得的一次性票据（提交登录时回传）
const captchaTicket = ref('')
// 滑块组件引用（登录失败后主动重置）
const sliderRef = ref(null)

// 读取「是否启用登录验证码」开关（尽力而为；失败时静默按未启用处理）
async function loadCaptchaStatus() {
  try {
    const data = await authApi.getCaptchaStatus()
    captchaEnabled.value = !!(data && data.enabled)
  } catch (e) {
    // 开关查询失败不阻断登录：保持未启用，由后端登录校验兜底
  }
}

// 滑块验证成功回调：记录票据
function onCaptchaSuccess(ticket) {
  captchaTicket.value = ticket || ''
}

// 滑块刷新/重置回调：清空已持有票据
function onCaptchaRefresh() {
  captchaTicket.value = ''
}

// 读取登录页品牌信息（尽力而为；失败时静默回退默认文案，request 已统一处理拒绝）
async function loadBrand() {
  try {
    const data = await authApi.getBrand()
    if (data && typeof data === 'object') {
      // 仅覆盖后端返回的非空字段，其余保留默认中文文案
      if (data.system_name) brand.system_name = data.system_name
      if (data.title) brand.title = data.title
      if (data.description) brand.description = data.description
    }
  } catch (e) {
    // 品牌读取失败不影响登录：保持默认文案即可（错误已由 request 统一处理）
  }
}

// 提交登录
async function onSubmit() {
  // 前端基础校验：用户名与密码不能为空（中文提示）
  const username = form.username.trim()
  if (!username) {
    showToast('请输入用户名', TOAST_TYPE.WARNING)
    return
  }
  if (!form.password) {
    showToast('请输入密码', TOAST_TYPE.WARNING)
    return
  }
  // 开启登录验证码时：必须先完成滑块验证（前端校验，后端二次兜底）
  if (captchaEnabled.value && !captchaTicket.value) {
    showToast('请先完成滑块验证', TOAST_TYPE.WARNING)
    return
  }

  submitting.value = true
  try {
    // 调用登录接口：成功返回 {token, user}；失败已由 request 统一弹窗提示
    const data = await authApi.login(username, form.password, captchaTicket.value)
    if (data && data.token) {
      userStore.setLogin(data.token, data.user || null)
      // 拉取菜单授权资源，供按权限渲染左侧菜单（需求 2.6）；失败不阻断登录。
      try {
        await userStore.loadAuthorizedResources()
      } catch (e) {
        // 授权资源拉取失败时保持 null（不限制），菜单交互层仍受后端接口判权保护
      }
      showToast('登录成功', TOAST_TYPE.SUCCESS)
      // 按 redirect 参数回跳，默认进入首页
      const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
      // 仅允许站内相对路径回跳，避免开放重定向风险
      const target = redirect && redirect.startsWith('/') ? redirect : '/'
      router.replace(target)
    }
  } catch (e) {
    // 登录失败（用户名或密码错误 / 验证票据失效等）已由 request 统一提示；
    // 票据为一次性，登录失败后须重置滑块，要求用户重新验证
    captchaTicket.value = ''
    if (captchaEnabled.value && sliderRef.value) {
      sliderRef.value.reset()
    }
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadBrand()
  loadCaptchaStatus()
})

// 跳转注册页
function goRegister() {
  router.push('/register')
}</script>

<template>
  <div class="login-page">
    <!-- 提交过程的全屏加载遮罩 + 转圈（规范 23） -->
    <Loading :visible="submitting" full-screen text="登录中..." />

    <div class="login-card">
      <!-- 品牌区：系统名称 / 标题 / 描述（需求 21.11；文本插值，规范 22） -->
      <div class="login-brand">
        <div class="login-brand__logo" aria-hidden="true">
          {{ brand.system_name.charAt(0) }}
        </div>
        <h1 class="login-brand__name">{{ brand.system_name }}</h1>
        <p class="login-brand__title">{{ brand.title }}</p>
        <p class="login-brand__desc">{{ brand.description }}</p>
      </div>

      <!-- 登录表单 -->
      <form class="login-form" @submit.prevent="onSubmit">
        <label class="login-field">
          <span class="login-field__label">用户名</span>
          <input
            v-model="form.username"
            class="login-field__input"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
            :disabled="submitting"
          />
        </label>

        <label class="login-field">
          <span class="login-field__label">密码</span>
          <input
            v-model="form.password"
            class="login-field__input"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            :disabled="submitting"
          />
        </label>

        <!-- 滑块拼图验证码（系统设置开启「启用登录验证码」时展示，需求 21.6） -->
        <div v-if="captchaEnabled" class="login-field">
          <span class="login-field__label">安全验证</span>
          <SliderCaptcha
            ref="sliderRef"
            :disabled="submitting"
            @success="onCaptchaSuccess"
            @refresh="onCaptchaRefresh"
          />
        </div>

        <button class="login-submit" type="submit" :disabled="submitting">
          {{ submitting ? '登录中...' : '登录' }}
        </button>

        <button
          class="login-register"
          type="button"
          :disabled="submitting"
          @click="goRegister"
        >
          注册账号
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  /* 蓝白主色调渐变背景（规范 25） */
  background: linear-gradient(135deg, var(--color-primary-light) 0%, var(--color-bg) 100%);
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 14px;
  box-shadow: var(--shadow-card);
  padding: 32px 28px;
}

/* 品牌区 */
.login-brand {
  text-align: center;
  margin-bottom: 24px;
}

.login-brand__logo {
  width: 56px;
  height: 56px;
  margin: 0 auto 12px;
  border-radius: 14px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  font-weight: 700;
}

.login-brand__name {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
}

.login-brand__title {
  margin-top: 6px;
  font-size: 15px;
  color: var(--color-text);
}

.login-brand__desc {
  margin-top: 4px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

/* 表单 */
.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login-field__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.login-field__input {
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

.login-field__input:focus {
  border-color: var(--color-primary);
}

.login-field__input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-submit {
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

.login-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.login-submit:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.login-register {
  margin-top: 4px;
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 0;
}

.login-register:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* 响应式：窄屏适配（规范 20） */
@media (max-width: 480px) {
  .login-card {
    padding: 24px 18px;
  }
}
</style>
