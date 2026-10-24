<!--
  免责声明页面（需求 21.12）
  职责：展示管理员配置的免责声明（标题、正文）。
  规范要点：加载遮罩 + 转圈（规范 23）、内容经 SafeHtml 防 XSS（规范 22）、
        全中文、响应式（规范 27/20）。
  说明：免责声明经系统设置接口获取（管理员配置）。普通用户若无该接口权限，
        则展示内置兜底文案，保证页面可用。
-->
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Loading, SafeHtml } from '@/components/common'
import { useUserStore } from '@/store/user_store'
import { getDisclaimer } from '@/api/settings_api'

const userStore = useUserStore()
const loading = ref(false)
// 默认兜底文案（管理员未配置或普通用户无权限读取设置时展示）
const disclaimer = reactive({
  title: '免责声明',
  content: '本系统仅供商家进行客服消息自动化管理使用。使用本系统所产生的一切后果由使用者自行承担，开发者不对因使用本系统造成的任何直接或间接损失负责。请遵守拼多多平台规则与相关法律法规。',
})

// 仅管理员可读取系统设置接口（需求 21.17）；普通用户展示内置兜底文案，
// 避免对普通用户触发「无访问权限」提示。
async function loadDisclaimer() {
  if (!userStore.isAdmin) {
    return
  }
  loading.value = true
  const [result] = await Promise.allSettled([getDisclaimer()])
  if (result.status === 'fulfilled' && result.value) {
    if (result.value.title) disclaimer.title = result.value.title
    if (result.value.content) disclaimer.content = result.value.content
  }
  loading.value = false
}

onMounted(loadDisclaimer)
</script>

<template>
  <div class="disclaimer-page">
    <h2 class="disclaimer-page__title">{{ disclaimer.title }}</h2>
    <section class="card">
      <SafeHtml :content="disclaimer.content" class="disclaimer-content" />
    </section>
    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.disclaimer-page {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.disclaimer-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}
.card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 20px;
}
.disclaimer-content {
  font-size: 14px;
  line-height: 1.9;
  color: var(--color-text);
}
</style>
