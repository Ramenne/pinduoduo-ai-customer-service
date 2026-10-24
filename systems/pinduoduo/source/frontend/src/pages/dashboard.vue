<!--
  仪表盘页面（需求 20.1 / 20.3）
  职责：展示关键指标卡片——启用店铺数、今日消息数、今日自动回复数、AI 回复数、风控触发数，
        统计口径为北京时间。数据经 backend /dashboard/overview 获取（后端统计，前端仅展示）。
  风格：与参考项目 xianyu-auto-reply-wangpan 一致的统计卡（stat-card）+ lucide 图标 + 页头。
  规范：加载时遮罩 + 转圈（规范 23）、全中文（规范 27）、响应式（规范 20）、
        错误提示由请求封装统一 showToast（规范 2/4）；不写死 localhost（规范 21）。
-->
<script setup>
import { onMounted, ref } from 'vue'
import { Store, MessageSquare, Bot, Sparkles, ShieldAlert, RefreshCw } from 'lucide-vue-next'
import { Loading } from '@/components/common'
import { fetchDashboardOverview } from '@/api/dashboard_api'
import { formatNumber } from '@/utils/format'

const loading = ref(false)

// 关键指标卡片定义（中文标题 + 取值键 + 图标 + 图标底色样式类），顺序即展示顺序
const metricDefs = [
  { key: 'enabled_shops', label: '启用店铺数', icon: Store, iconClass: 'stat-icon-primary' },
  { key: 'today_messages', label: '今日消息数', icon: MessageSquare, iconClass: 'stat-icon-success' },
  { key: 'today_auto_replies', label: '今日自动回复数', icon: Bot, iconClass: 'stat-icon-info' },
  { key: 'today_ai_replies', label: 'AI 回复数', icon: Sparkles, iconClass: 'stat-icon-warning' },
  { key: 'today_risk_triggers', label: '风控触发数', icon: ShieldAlert, iconClass: 'stat-icon-danger' },
]

const overview = ref({
  enabled_shops: 0,
  today_messages: 0,
  today_auto_replies: 0,
  today_ai_replies: 0,
  today_risk_triggers: 0,
})

async function loadOverview() {
  loading.value = true
  try {
    const data = await fetchDashboardOverview()
    if (data) {
      overview.value = { ...overview.value, ...data, enabled_shops: data.enabled_shops ?? data.online_shops ?? 0 }
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadOverview)
</script>

<template>
  <div>
    <!-- 页头：标题 + 刷新 -->
    <div class="page-header flex-between flex-wrap gap-4">
      <div>
        <h1 class="page-title">仪表盘</h1>
        <p class="page-description">关键运营指标总览（北京时间）</p>
      </div>
      <button type="button" class="btn-ios-secondary" :disabled="loading" @click="loadOverview">
        <RefreshCw class="w-4 h-4" :class="{ 'animate-spin': loading }" />
        刷新数据
      </button>
    </div>

    <!-- 关键指标卡片区（相对定位以承载区域加载遮罩） -->
    <div class="relative min-h-[120px]">
      <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3 sm:gap-4">
        <div v-for="metric in metricDefs" :key="metric.key" class="stat-card">
          <div :class="metric.iconClass">
            <component :is="metric.icon" class="w-5 h-5" />
          </div>
          <div class="min-w-0">
            <p class="stat-value">{{ formatNumber(overview[metric.key]) }}</p>
            <p class="stat-label truncate">{{ metric.label }}</p>
          </div>
        </div>
      </div>

      <!-- 区域加载遮罩 + 转圈（规范 23） -->
      <Loading :visible="loading" text="加载中..." />
    </div>
  </div>
</template>
