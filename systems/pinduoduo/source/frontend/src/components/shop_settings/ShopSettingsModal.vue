<!--
  店铺设置弹窗（店铺管理行内「设置」入口）
  职责：将原本拆分为独立菜单的「按店铺维度」设置（默认与商品专属回复、AI 设置、营业时间、
        消息过滤与黑名单、风控管理、转人工设置）集中到店铺管理页的行内弹窗中配置，
        不再为这些店铺级设置单独开菜单（参照 xianyu-auto-reply-wangpan 账号管理的做法）。
  规范：弹窗仅关闭按钮关闭（规范 7，遮罩不可关闭）；文案全中文（规范 27）；
        各设置面板自带保存按钮，故隐藏弹窗底部操作栏。
  说明：左侧为设置分类标签，右侧渲染对应面板；切换店铺或重新打开时通过 key 强制重建面板，
        确保各面板按当前 shopPk 重新拉取数据。
-->
<script setup>
import { computed, ref, watch } from 'vue'
import { FormModal } from '@/components/common'
import DefaultReplyPanel from './DefaultReplyPanel.vue'
import AiSettingsPanel from './AiSettingsPanel.vue'
import BusinessHoursPanel from './BusinessHoursPanel.vue'
import MessageFiltersPanel from './MessageFiltersPanel.vue'
import RiskControlPanel from './RiskControlPanel.vue'
import TransferPanel from './TransferPanel.vue'
import NotifyChannelsPanel from './NotifyChannelsPanel.vue'

const props = defineProps({
  // 是否显示（v-model）
  modelValue: {
    type: Boolean,
    default: false,
  },
  // 当前操作的店铺对象（含 id / shop_id / shop_name）
  shop: {
    type: Object,
    default: null,
  },
})

defineEmits(['update:modelValue'])

// 设置分类标签：key 与面板组件映射
const TABS = [
  { key: 'default-reply', label: '默认与商品回复', component: DefaultReplyPanel },
  { key: 'ai-settings', label: 'AI 设置', component: AiSettingsPanel },
  { key: 'business-hours', label: '营业时间', component: BusinessHoursPanel },
  { key: 'message-filters', label: '消息过滤与黑名单', component: MessageFiltersPanel },
  { key: 'risk-control', label: '风控管理', component: RiskControlPanel },
  { key: 'transfer', label: '转人工设置', component: TransferPanel },
  { key: 'notify-channels', label: '通知渠道', component: NotifyChannelsPanel },
]

const activeTab = ref('default-reply')

// 当前店铺主键
const shopPk = computed(() => (props.shop ? props.shop.id : ''))

// 弹窗标题：附带店铺名称便于辨识
const title = computed(() => {
  if (!props.shop) {
    return '店铺设置'
  }
  const name = props.shop.shop_name || props.shop.shop_id || `店铺#${props.shop.id}`
  return `店铺设置 - ${name}`
})

// 当前激活面板组件
const activeComponent = computed(() => {
  const found = TABS.find((tab) => tab.key === activeTab.value)
  return found ? found.component : null
})

// 每次打开弹窗时重置到第一个标签
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      activeTab.value = 'default-reply'
    }
  },
)
</script>

<template>
  <FormModal
    :model-value="modelValue"
    :title="title"
    :show-footer="false"
    max-width="860px"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="shop-settings">
      <!-- 左侧设置分类 -->
      <nav class="shop-settings__nav">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          type="button"
          class="shop-settings__nav-item"
          :class="{ 'shop-settings__nav-item--active': activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </nav>

      <!-- 右侧面板：切换店铺/标签时以 key 强制重建，确保按当前店铺重新加载 -->
      <div class="shop-settings__content">
        <component
          :is="activeComponent"
          v-if="shopPk"
          :key="`${activeTab}-${shopPk}`"
          :shop-pk="shopPk"
        />
        <p v-else class="shop-settings__empty">请先选择店铺</p>
      </div>
    </div>
  </FormModal>
</template>

<style scoped>
.shop-settings {
  display: flex;
  gap: 16px;
  min-height: 420px;
}

/* 左侧分类导航 */
.shop-settings__nav {
  flex: 0 0 150px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-right: 1px solid var(--color-border);
  padding-right: 12px;
}

.shop-settings__nav-item {
  text-align: left;
  padding: 9px 12px;
  font-size: 14px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--color-text);
  cursor: pointer;
}

.shop-settings__nav-item:hover {
  background: var(--color-hover-bg, rgba(0, 0, 0, 0.04));
}

.shop-settings__nav-item--active,
.shop-settings__nav-item--active:hover {
  background: var(--color-primary);
  color: var(--color-on-primary);
}

/* 右侧内容区 */
.shop-settings__content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  max-height: 64vh;
}

.shop-settings__empty {
  color: var(--color-text-secondary);
  font-size: 14px;
}

/* 响应式：移动端上下布局 */
@media (max-width: 767px) {
  .shop-settings {
    flex-direction: column;
  }
  .shop-settings__nav {
    flex: none;
    flex-direction: row;
    flex-wrap: wrap;
    border-right: none;
    border-bottom: 1px solid var(--color-border);
    padding-right: 0;
    padding-bottom: 10px;
  }
  .shop-settings__content {
    max-height: none;
  }
}
</style>
