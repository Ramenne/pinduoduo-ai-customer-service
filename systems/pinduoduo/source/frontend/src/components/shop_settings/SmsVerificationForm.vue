<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { shopApi } from '@/api'
const props = defineProps({ sessionId: { type: String, required: true }, verification: { type: Object, required: true } })
const code = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
const remaining = ref(0)
watch(() => props.verification.cooldown_seconds, value => { remaining.value = Number(value) || 0 }, { immediate: true })
const timer = setInterval(() => { if (remaining.value > 0) remaining.value-- }, 1000)
onUnmounted(() => { clearInterval(timer); code.value = '' })
async function act(action) {
  if (busy.value) return
  if (action === 'resend_sms' && (remaining.value > 0 || !props.verification.can_resend)) return
  if (action === 'submit_sms' && !/^[0-9]{4,8}$/.test(code.value)) {
    error.value = '请输入 4～8 位数字验证码'
    return
  }
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await shopApi.submitLoginVerification(props.sessionId,
      action === 'submit_sms' ? { action, code: code.value } : { action })
    message.value = action === 'submit_sms' ? '验证码已提交，正在等待验证结果…' : '已请求重新发送，请留意手机短信。'
    if (action === 'submit_sms') code.value = ''
    else remaining.value = 2
  } catch (e) {
    error.value = e?.message || '操作未完成，请稍后重试'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <form class="sms-form" @submit.prevent="act('submit_sms')">
    <p class="sms-title">手机短信验证</p>
    <p v-if="verification.masked_mobile">请填写发送至 <b>{{ verification.masked_mobile }}</b> 的验证码。</p>
    <p v-else>请填写绑定手机收到的短信验证码。</p>
    <label for="login-sms-code">短信验证码</label>
    <input id="login-sms-code" v-model="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="8" placeholder="输入短信验证码" :disabled="busy" />
    <p v-if="error || verification.error" role="alert" class="sms-error">{{ error || verification.error }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <div class="sms-actions">
      <button type="submit" class="sms-confirm" :disabled="busy || !code">{{ busy ? '正在提交…' : '确认验证码' }}</button>
      <button type="button" :disabled="busy || remaining > 0 || !verification.can_resend" @click="act('resend_sms')">{{ remaining > 0 ? `${remaining} 秒后重发` : '重新发送' }}</button>
    </div>
  </form>
</template>
<style scoped>
.sms-form { display: flex; flex-direction: column; gap: 10px; padding: 16px; border: 1px solid #dce6f5; border-radius: 10px; background: #f7faff; }
.sms-form p { margin: 0; font-size: 13px; line-height: 1.6; }
.sms-form .sms-title { font-size: 16px; font-weight: 600; }
.sms-form label { font-size: 13px; }
.sms-form input { min-height: 44px; padding: 10px 12px; background: white; color: #17233a; border: 1px solid #bccbe1; border-radius: 6px; font-size: 16px; }
.sms-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.sms-actions button { min-height: 40px; padding: 8px 14px; border: 1px solid #c8d4e4; border-radius: 6px; background: white; }
.sms-actions .sms-confirm { background: #347df4; border-color: #347df4; color: white; }
.sms-actions button:disabled { opacity: .5; cursor: not-allowed; }
.sms-error { color: #cf2e2e; }
</style>
