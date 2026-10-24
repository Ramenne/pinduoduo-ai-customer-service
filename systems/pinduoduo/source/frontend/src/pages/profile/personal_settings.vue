<!--
  个人设置页面（拼多多自动回复系统前端，任务 17.1）
  职责（需求 22.1/22.3/22.4/22.5/22.6/22.7）：
    1. 账户信息：展示当前用户的用户名与角色，均为只读（需求 22.1）；
    2. 修改密码：前端先行校验「新密码长度 < 6 位」或「两次输入不一致」时阻止提交并提示（需求 22.4），
       校验通过调用后端；后端「当前密码错误」提示由统一响应体经 showToast 展示（需求 22.3）；
       修改成功后令牌失效，引导重新登录（需求 22.5）；
    3. 联系方式：维护微信、QQ，按用户维度持久化（需求 22.6/22.7）。
  规范遵循：
    - 提示统一使用 showToast，禁止 alert（规范 6）；
    - 加载与提交过程展示加载遮罩 + 转圈（规范 23）；
    - 全中文文案（规范 27）；响应式适配（规范 20）；
    - 请求经 @/utils/request（地址由环境变量配置，禁止写死 localhost，规范 21）；
    - 文本插值渲染，不使用 v-html（规范 22）。
-->
<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '@/api'
import { useUserStore } from '@/store'
import { Loading } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'

const router = useRouter()
const userStore = useUserStore()

// 新密码最小长度（与后端兜底校验一致，需求 22.4）
const NEW_PASSWORD_MIN_LENGTH = 6

// 页面级加载状态（拉取账户信息时展示遮罩）
const loading = ref(false)

// 账户信息（只读展示，需求 22.1）
const account = reactive({
  username: '',
  roleName: '',
})

// 修改密码表单（需求 22.2/22.4）
const passwordForm = reactive({
  currentPassword: '',
  newPassword: '',
  confirmPassword: '',
})
// 修改密码提交中状态
const passwordSubmitting = ref(false)

// 联系方式表单（需求 22.6）
const contactForm = reactive({
  wechat: '',
  qq: '',
})
// 联系方式提交中状态
const contactSubmitting = ref(false)

// 将后端用户信息填充到本页展示字段
function fillFromUser(user) {
  if (!user || typeof user !== 'object') {
    return
  }
  account.username = user.username || ''
  account.roleName = user.role_name || (user.is_admin ? '管理员' : '普通用户')
  contactForm.wechat = user.wechat || ''
  contactForm.qq = user.qq || ''
}

// 拉取当前用户账户信息（需求 22.1）
async function loadProfile() {
  loading.value = true
  try {
    const data = await authApi.getProfile()
    if (data) {
      fillFromUser(data)
      // 同步刷新本地用户信息，保证右上角用户菜单展示一致
      userStore.setUserInfo(data)
    }
  } catch (e) {
    // 失败已由 request 统一提示
  } finally {
    loading.value = false
  }
}

// 提交修改密码（前端校验 + 调用后端）
async function onSubmitPassword() {
  // 当前密码不能为空
  if (!passwordForm.currentPassword) {
    showToast('请输入当前密码', TOAST_TYPE.WARNING)
    return
  }
  // 需求 22.4：新密码长度小于 6 位时阻止提交并提示
  if (passwordForm.newPassword.length < NEW_PASSWORD_MIN_LENGTH) {
    showToast(`新密码长度不能少于 ${NEW_PASSWORD_MIN_LENGTH} 位`, TOAST_TYPE.WARNING)
    return
  }
  // 需求 22.4：两次输入不一致时阻止提交并提示
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    showToast('两次输入的新密码不一致', TOAST_TYPE.WARNING)
    return
  }

  passwordSubmitting.value = true
  try {
    // 校验通过后调用后端；当前密码错误等业务失败已由 request 统一提示（需求 22.3）
    await authApi.changePassword(passwordForm.currentPassword, passwordForm.newPassword)
    showToast('密码修改成功，请重新登录', TOAST_TYPE.SUCCESS)
    // 需求 22.5：密码修改成功后令牌已失效，清理本地登录态并引导重新登录
    userStore.clearUser()
    router.replace('/login')
  } catch (e) {
    // 业务失败已由 request 统一提示
  } finally {
    passwordSubmitting.value = false
  }
}

// 提交联系方式（需求 22.6）
async function onSubmitContact() {
  contactSubmitting.value = true
  try {
    const data = await authApi.updateContact({
      wechat: contactForm.wechat,
      qq: contactForm.qq,
    })
    showToast('联系方式保存成功', TOAST_TYPE.SUCCESS)
    if (data) {
      fillFromUser(data)
      userStore.setUserInfo(data)
    }
  } catch (e) {
    // 业务失败已由 request 统一提示
  } finally {
    contactSubmitting.value = false
  }
}

onMounted(() => {
  // 先以本地缓存的用户信息快速填充，再拉取最新数据
  fillFromUser(userStore.userInfo)
  loadProfile()
})
</script>

<template>
  <div class="profile-page">
    <!-- 页面加载遮罩 + 转圈（规范 23） -->
    <Loading :visible="loading" text="加载中..." />

    <!-- 账户信息（只读，需求 22.1） -->
    <section class="profile-card">
      <h2 class="profile-card__title">账户信息</h2>
      <div class="profile-row">
        <span class="profile-row__label">用户名</span>
        <span class="profile-row__value">{{ account.username || '-' }}</span>
      </div>
      <div class="profile-row">
        <span class="profile-row__label">角色</span>
        <span class="profile-row__value">{{ account.roleName || '-' }}</span>
      </div>
      <p class="profile-card__hint">用户名与角色为只读信息，如需调整请联系管理员。</p>
    </section>

    <!-- 修改密码（需求 22.2/22.3/22.4/22.5） -->
    <section class="profile-card">
      <h2 class="profile-card__title">修改密码</h2>
      <form class="profile-form" @submit.prevent="onSubmitPassword">
        <label class="profile-field">
          <span class="profile-field__label">当前密码</span>
          <input
            v-model="passwordForm.currentPassword"
            class="profile-field__input"
            type="password"
            autocomplete="current-password"
            placeholder="请输入当前密码"
            :disabled="passwordSubmitting"
          />
        </label>
        <label class="profile-field">
          <span class="profile-field__label">新密码</span>
          <input
            v-model="passwordForm.newPassword"
            class="profile-field__input"
            type="password"
            autocomplete="new-password"
            placeholder="请输入新密码（不少于 6 位）"
            :disabled="passwordSubmitting"
          />
        </label>
        <label class="profile-field">
          <span class="profile-field__label">确认新密码</span>
          <input
            v-model="passwordForm.confirmPassword"
            class="profile-field__input"
            type="password"
            autocomplete="new-password"
            placeholder="请再次输入新密码"
            :disabled="passwordSubmitting"
          />
        </label>
        <div class="profile-form__actions">
          <button class="profile-btn" type="submit" :disabled="passwordSubmitting">
            {{ passwordSubmitting ? '提交中...' : '保存新密码' }}
          </button>
        </div>
      </form>
    </section>

    <!-- 联系方式（需求 22.6/22.7） -->
    <section class="profile-card">
      <h2 class="profile-card__title">联系方式</h2>
      <form class="profile-form" @submit.prevent="onSubmitContact">
        <label class="profile-field">
          <span class="profile-field__label">微信</span>
          <input
            v-model="contactForm.wechat"
            class="profile-field__input"
            type="text"
            placeholder="请输入微信号"
            :disabled="contactSubmitting"
          />
        </label>
        <label class="profile-field">
          <span class="profile-field__label">QQ</span>
          <input
            v-model="contactForm.qq"
            class="profile-field__input"
            type="text"
            placeholder="请输入 QQ 号"
            :disabled="contactSubmitting"
          />
        </label>
        <div class="profile-form__actions">
          <button class="profile-btn" type="submit" :disabled="contactSubmitting">
            {{ contactSubmitting ? '提交中...' : '保存联系方式' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.profile-page {
  position: relative;
  max-width: 640px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.profile-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  box-shadow: var(--shadow-card);
  padding: 20px;
}

.profile-card__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 16px;
}

.profile-card__hint {
  margin-top: 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

/* 只读信息行 */
.profile-row {
  display: flex;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px dashed var(--color-border);
}

.profile-row:last-of-type {
  border-bottom: none;
}

.profile-row__label {
  width: 96px;
  flex-shrink: 0;
  font-size: 14px;
  color: var(--color-text-secondary);
}

.profile-row__value {
  font-size: 14px;
  color: var(--color-text);
  word-break: break-all;
}

/* 表单 */
.profile-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.profile-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.profile-field__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.profile-field__input {
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

.profile-field__input:focus {
  border-color: var(--color-primary);
}

.profile-field__input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.profile-form__actions {
  display: flex;
  justify-content: flex-end;
}

.profile-btn {
  height: 38px;
  padding: 0 20px;
  border: none;
  border-radius: 8px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}

.profile-btn:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.profile-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

/* 响应式：窄屏时只读行纵向排列（规范 20） */
@media (max-width: 480px) {
  .profile-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
  }
  .profile-row__label {
    width: auto;
  }
}
</style>
