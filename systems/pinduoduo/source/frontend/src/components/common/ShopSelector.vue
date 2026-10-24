<!--
  店铺选择器组件（ShopSelector）
  职责：多数自动回复 / 知识库配置均按「店铺维度」管理，故抽取统一的店铺下拉选择器，
        避免在各页面重复实现店铺拉取与下拉渲染（规范 36：同一逻辑不重复实现）。
  特性：
    - 挂载时拉取启用店铺列表（后端分页接口，仅取启用店铺），加载期间禁用并提示；
    - 拉取成功后默认选中第一项并通过 v-model 抛出其主键，便于父页面立即加载数据；
    - 文案全中文（规范 27）。
  用法（v-model 绑定选中的店铺主键 shop.id）：
    <ShopSelector v-model="shopPk" @change="onShopChange" />
-->
<script setup>
import { ref, computed, onMounted } from 'vue'
import Select from './Select.vue'
import { shopApi } from '@/api'

const props = defineProps({
  // 当前选中的店铺主键（v-model）
  modelValue: {
    type: [Number, String],
    default: '',
  },
})

const emit = defineEmits(['update:modelValue', 'change'])

// 店铺下拉选项：{ value: shop.id, label: 店铺名称 }
const shopOptions = ref([])
// 拉取中标志（加载期间禁用下拉）
const loading = ref(false)

// 下拉选项（无店铺时给出占位提示）
const options = computed(() => shopOptions.value)

// 拉取启用店铺列表并初始化默认选中项
async function loadShops() {
  loading.value = true
  // 仅取启用店铺（status=1）作为可配置对象
  const data = await shopApi.fetchShops({ page: 1, page_size: 100, status: 1 }).catch(() => null)
  loading.value = false
  if (!data) {
    return
  }
  const list = Array.isArray(data.list) ? data.list : []
  shopOptions.value = list.map((item) => ({
    value: item.id,
    label: item.shop_name || item.shop_id || `店铺#${item.id}`,
  }))
  // 默认选中第一项，便于父页面立即按店铺加载数据
  if (!props.modelValue && shopOptions.value.length > 0) {
    const firstValue = shopOptions.value[0].value
    emit('update:modelValue', firstValue)
    emit('change', firstValue)
  }
}

// 选中变化时同步父组件
function onSelect(value) {
  emit('update:modelValue', value)
  emit('change', value)
}

onMounted(loadShops)
</script>

<template>
  <div class="shop-selector">
    <span class="shop-selector__label">店铺：</span>
    <div class="shop-selector__control">
      <Select
        :model-value="modelValue"
        :options="options"
        :disabled="loading"
        :placeholder="loading ? '店铺加载中...' : '请选择店铺'"
        @change="onSelect"
      />
    </div>
  </div>
</template>

<style scoped>
.shop-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}

.shop-selector__label {
  font-size: 14px;
  color: var(--color-text);
  white-space: nowrap;
}

.shop-selector__control {
  width: 220px;
}
</style>
