<!--
  风控管理面板（店铺管理「设置」弹窗内嵌，需求 13.1/13.4）
  职责：按店铺维度配置风控规则（回复频率上限与统计窗口），并展示风控类型枚举（中文）。
  说明：店铺由父级弹窗经 shopPk 传入；风控类型枚举从后端字典查出中文展示（需求 13.4）。
-->
<script setup>
import { reactive, ref, onMounted } from 'vue'
import { Loading } from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { autoReplyApi } from '@/api'

const props = defineProps({
  shopPk: {
    type: [Number, String],
    default: '',
  },
})

const loading = ref(false)
const saving = ref(false)

// 风控类型枚举（中文文案，需求 13.4）
const riskTypes = ref([])

// 表单：频率上限与窗口（空字符串表示不限制）
const form = reactive({
  session_reply_limit: '',
  shop_reply_limit: '',
  window_seconds: '',
  enabled: true,
})

// 将可空数值字段归一化：空字符串 -> null，否则 -> 数值
function toNullableInt(value) {
  if (value === '' || value === null || value === undefined) {
    return null
  }
  const num = Number(value)
  return Number.isNaN(num) ? null : num
}

// 校验非负整数
function isValidLimit(value) {
  if (value === '' || value === null) {
    return true
  }
  const num = Number(value)
  return Number.isInteger(num) && num >= 0
}

// 拉取风控规则
async function loadRule() {
  if (!props.shopPk) {
    return
  }
  loading.value = true
  const data = await autoReplyApi.getRiskRule(props.shopPk).catch(() => null)
  loading.value = false
  if (!data) {
    Object.assign(form, {
      session_reply_limit: '',
      shop_reply_limit: '',
      window_seconds: '',
      enabled: true,
    })
    return
  }
  Object.assign(form, {
    session_reply_limit: data.session_reply_limit ?? '',
    shop_reply_limit: data.shop_reply_limit ?? '',
    window_seconds: data.window_seconds ?? '',
    enabled: data.enabled !== false,
  })
}

// 拉取风控类型枚举（中文）
async function loadRiskTypes() {
  const data = await autoReplyApi.listRiskTypes().catch(() => null)
  if (!data) {
    return
  }
  // 后端可能返回 { list: [...] } 或直接数组，统一兼容
  const list = Array.isArray(data) ? data : Array.isArray(data.list) ? data.list : []
  riskTypes.value = list.map((item) => ({
    key: item.dict_key ?? item.key ?? item.value,
    label: item.dict_label ?? item.label ?? item.text,
  }))
}

// 保存风控规则
async function save() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  if (
    !isValidLimit(form.session_reply_limit) ||
    !isValidLimit(form.shop_reply_limit) ||
    !isValidLimit(form.window_seconds)
  ) {
    showToast('频率上限与统计窗口需为非负整数', TOAST_TYPE.WARNING)
    return
  }
  const payload = {
    session_reply_limit: toNullableInt(form.session_reply_limit),
    shop_reply_limit: toNullableInt(form.shop_reply_limit),
    window_seconds: toNullableInt(form.window_seconds),
    enabled: form.enabled,
  }
  saving.value = true
  const result = await autoReplyApi.saveRiskRule(props.shopPk, payload).catch(() => null)
  saving.value = false
  if (result === null) {
    return
  }
  showToast('保存成功', TOAST_TYPE.SUCCESS)
}

onMounted(async () => {
  await loadRiskTypes()
  await loadRule()
})
</script>

<template>
  <div class="panel">
    <p class="panel__desc">配置回复频率上限与统计窗口，超过上限将暂停自动回复并记录风控日志。</p>
    <div class="panel__body">
      <Loading :visible="loading" text="加载中..." />
      <label class="field field--inline">
        <input v-model="form.enabled" type="checkbox" />
        <span class="field__label">启用风控规则</span>
      </label>
      <label class="field">
        <span class="field__label">单会话回复次数上限（空表示不限制）</span>
        <input v-model="form.session_reply_limit" class="field__input" type="number" min="0" step="1" placeholder="如：10" />
      </label>
      <label class="field">
        <span class="field__label">单店铺回复次数上限（空表示不限制）</span>
        <input v-model="form.shop_reply_limit" class="field__input" type="number" min="0" step="1" placeholder="如：100" />
      </label>
      <label class="field">
        <span class="field__label">统计窗口（秒，空表示不限制）</span>
        <input v-model="form.window_seconds" class="field__input" type="number" min="0" step="1" placeholder="如：60" />
      </label>
      <div class="panel__actions">
        <button class="btn btn--primary" type="button" :disabled="saving || !shopPk" @click="save">
          {{ saving ? '保存中...' : '保存' }}
        </button>
      </div>

      <!-- 风控类型枚举说明（中文展示，需求 13.4） -->
      <div v-if="riskTypes.length" class="risk-types">
        <h4 class="risk-types__title">风控类型说明</h4>
        <ul class="risk-types__list">
          <li v-for="item in riskTypes" :key="item.key">{{ item.label }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import '@/components/shop_settings/panel.css';

.risk-types {
  margin-top: 16px;
}
.risk-types__title {
  font-size: 14px;
  color: var(--color-text);
  margin-bottom: 8px;
}
.risk-types__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  list-style: none;
}
.risk-types__list li {
  padding: 4px 12px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-primary-light);
  border-radius: 12px;
}
</style>
