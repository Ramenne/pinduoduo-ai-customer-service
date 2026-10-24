<!--
  关于页面（需求 21.5/21.13/21.3）
  职责：展示系统简介、版本信息、联系二维码（管理员上传），以及当前生效的公告。
  规范要点：加载遮罩 + 转圈（规范 23）、用户/管理员输入经 SafeHtml 防 XSS（规范 22）、
        全中文、响应式（规范 27/20）。图片地址不写死域名，使用后端返回的 URL。
-->
<script setup>
import { ref, onMounted } from 'vue'
import { Loading, SafeHtml } from '@/components/common'
import { useUserStore } from '@/store/user_store'
import { formatDateTime } from '@/utils/format'
import { getQrcodes } from '@/api/settings_api'
import { listVisibleAnnouncements } from '@/api/announcements_api'

const userStore = useUserStore()
const loading = ref(false)
const qrcodes = ref([])
const announcements = ref([])

// 二维码类型中文文案
const QR_TYPE_LABEL = {
  wechat: '微信',
  qq: 'QQ',
  mp: '微信公众号',
  telegram: 'Telegram',
}

function qrTypeLabel(type) {
  return QR_TYPE_LABEL[type] || type
}

async function loadData() {
  loading.value = true
  // 公告展示对所有登录用户开放；二维码为管理员设置接口，仅管理员读取，
  // 避免对普通用户触发「无访问权限」提示（需求 21.17）。
  const tasks = [listVisibleAnnouncements({ page: 1, page_size: 20 })]
  if (userStore.isAdmin) {
    tasks.push(getQrcodes())
  }
  const results = await Promise.allSettled(tasks)
  const annResult = results[0]
  if (annResult.status === 'fulfilled' && annResult.value) {
    announcements.value = annResult.value.list || []
  }
  if (userStore.isAdmin) {
    const qrResult = results[1]
    if (qrResult && qrResult.status === 'fulfilled' && qrResult.value) {
      qrcodes.value = qrResult.value.items || []
    }
  }
  loading.value = false
}

onMounted(loadData)
</script>

<template>
  <div class="about-page">
    <h2 class="about-page__title">关于</h2>

    <!-- 系统简介 -->
    <section class="card">
      <h3 class="card__title">系统简介</h3>
      <p class="about-text">
        拼多多自动回复管理系统，面向拼多多多店铺商家，提供基于商家后台 WebSocket 的客服消息
        自动化能力，覆盖账号与店铺管理、消息收发与自动回复、AI 智能回复与知识库、商品管理、
        会话转移转人工、风控与日志、用户与权限、系统设置等业务。
      </p>
      <p class="about-meta">版本：v1.0.0</p>
    </section>

    <!-- 当前公告 -->
    <section class="card">
      <h3 class="card__title">系统公告</h3>
      <ul v-if="announcements.length > 0" class="ann-list">
        <li v-for="ann in announcements" :key="ann.id" class="ann-item">
          <div class="ann-item__title"><SafeHtml :content="ann.title" /></div>
          <div class="ann-item__content"><SafeHtml :content="ann.content" /></div>
          <div class="ann-item__time">{{ formatDateTime(ann.publish_at || ann.created_at) }}</div>
        </li>
      </ul>
      <p v-else class="empty">暂无公告</p>
    </section>

    <!-- 联系二维码 -->
    <section v-if="qrcodes.length > 0" class="card">
      <h3 class="card__title">联系我们</h3>
      <div class="qr-grid">
        <div v-for="(qr, index) in qrcodes" :key="index" class="qr-card">
          <img :src="qr.image_url" :alt="qrTypeLabel(qr.type)" class="qr-img" />
          <span class="qr-label">{{ qrTypeLabel(qr.type) }}</span>
        </div>
      </div>
    </section>

    <Loading :visible="loading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
.about-page {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.about-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}
.card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
.about-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text);
}
.about-meta {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.ann-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ann-item {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 12px;
}
.ann-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.ann-item__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 4px;
}
.ann-item__content {
  font-size: 13px;
  color: var(--color-text);
  line-height: 1.6;
}
.ann-item__time {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 4px;
}
.empty {
  font-size: 14px;
  color: var(--color-text-secondary);
}
.qr-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}
.qr-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.qr-img {
  width: 140px;
  height: 140px;
  object-fit: contain;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: #fff;
}
.qr-label {
  font-size: 13px;
  color: var(--color-text);
}
</style>
