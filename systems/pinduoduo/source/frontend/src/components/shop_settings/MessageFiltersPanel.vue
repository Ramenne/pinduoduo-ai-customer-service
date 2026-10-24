<!--
  消息过滤与黑名单面板（店铺管理「设置」弹窗内嵌，需求 12.1/12.3/12.5/12.6）
  职责：按店铺维度管理「消息过滤规则」与「黑名单」（标签切换，后端分页）。
  说明：店铺由父级弹窗经 shopPk 传入；新增弹窗仅关闭按钮关闭（规范 7）；
        用户输入经 SafeHtml 转义（规范 22）、文案全中文（规范 27）。
-->
<script setup>
import { reactive, ref, onMounted } from 'vue'
import {
  TableContainer,
  Pagination,
  Loading,
  ConfirmModal,
  FormModal,
  Select,
  SafeHtml,
} from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { autoReplyApi } from '@/api'

const props = defineProps({
  shopPk: {
    type: [Number, String],
    default: '',
  },
})

// 当前标签：filter（过滤规则） / blacklist（黑名单）
const activeTab = ref('filter')

// ===== 过滤规则状态 =====
const filterList = ref([])
const filterTotal = ref(0)
const filterPage = ref(1)
const filterPageSize = ref(20)
const filterLoading = ref(false)

// 过滤规则条件类型选项（与后端枚举一致）
const conditionTypeOptions = [
  { value: 'contains', label: '包含关键词' },
  { value: 'regex', label: '正则匹配' },
  { value: 'msg_type', label: '消息类型' },
]

// 新增过滤规则弹窗
const filterModalVisible = ref(false)
const filterSaving = ref(false)
const filterForm = reactive({ condition_type: 'contains', condition_value: '' })

// ===== 黑名单状态 =====
const blackList = ref([])
const blackTotal = ref(0)
const blackPage = ref(1)
const blackPageSize = ref(20)
const blackLoading = ref(false)

// 加入黑名单弹窗
const blackModalVisible = ref(false)
const blackSaving = ref(false)
const blackForm = reactive({ customer_uid: '' })

// 移出黑名单确认
const removeVisible = ref(false)
const removing = ref(false)
const removeTarget = ref(null)

// 条件类型中文展示
function conditionTypeLabel(type) {
  const found = conditionTypeOptions.find((opt) => opt.value === type)
  return found ? found.label : type
}

// ---------------- 过滤规则 ----------------
async function loadFilters() {
  if (!props.shopPk) {
    filterList.value = []
    filterTotal.value = 0
    return
  }
  filterLoading.value = true
  const data = await autoReplyApi
    .listFilterRules({ shop_pk: props.shopPk, page: filterPage.value, page_size: filterPageSize.value })
    .catch(() => null)
  filterLoading.value = false
  if (!data) {
    return
  }
  filterList.value = Array.isArray(data.list) ? data.list : []
  filterTotal.value = data.total || 0
}

function openFilterCreate() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  Object.assign(filterForm, { condition_type: 'contains', condition_value: '' })
  filterModalVisible.value = true
}

async function submitFilter() {
  if (!filterForm.condition_value.trim()) {
    showToast('请填写过滤条件值', TOAST_TYPE.WARNING)
    return
  }
  filterSaving.value = true
  const result = await autoReplyApi
    .createFilterRule({
      shop_pk: props.shopPk,
      condition_type: filterForm.condition_type,
      condition_value: filterForm.condition_value.trim(),
      enabled: true,
    })
    .catch(() => null)
  filterSaving.value = false
  if (result === null) {
    return
  }
  showToast('新增成功', TOAST_TYPE.SUCCESS)
  filterModalVisible.value = false
  filterPage.value = 1
  loadFilters()
}

async function toggleFilterStatus(row) {
  const nextEnabled = !(row.enabled !== false)
  const result = await autoReplyApi.setFilterRuleStatus(row.id, nextEnabled).catch(() => null)
  if (result === null) {
    return
  }
  showToast(nextEnabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
  loadFilters()
}

function onFilterPageChange(value) {
  filterPage.value = value
  loadFilters()
}
function onFilterPageSizeChange(value) {
  filterPageSize.value = value
  filterPage.value = 1
  loadFilters()
}

// ---------------- 黑名单 ----------------
async function loadBlacklist() {
  if (!props.shopPk) {
    blackList.value = []
    blackTotal.value = 0
    return
  }
  blackLoading.value = true
  const data = await autoReplyApi
    .listBlacklist({ shop_pk: props.shopPk, page: blackPage.value, page_size: blackPageSize.value })
    .catch(() => null)
  blackLoading.value = false
  if (!data) {
    return
  }
  blackList.value = Array.isArray(data.list) ? data.list : []
  blackTotal.value = data.total || 0
}

function openBlackCreate() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  blackForm.customer_uid = ''
  blackModalVisible.value = true
}

async function submitBlack() {
  if (!blackForm.customer_uid.trim()) {
    showToast('请填写客户标识 customer_uid', TOAST_TYPE.WARNING)
    return
  }
  blackSaving.value = true
  const result = await autoReplyApi
    .addToBlacklist({ shop_pk: props.shopPk, customer_uid: blackForm.customer_uid.trim() })
    .catch(() => null)
  blackSaving.value = false
  if (result === null) {
    return
  }
  showToast('已加入黑名单', TOAST_TYPE.SUCCESS)
  blackModalVisible.value = false
  blackPage.value = 1
  loadBlacklist()
}

function openRemove(row) {
  removeTarget.value = row
  removeVisible.value = true
}

async function confirmRemove() {
  if (!removeTarget.value) {
    return
  }
  removing.value = true
  const result = await autoReplyApi.removeFromBlacklist(removeTarget.value.id).catch(() => null)
  removing.value = false
  if (result === null) {
    return
  }
  showToast('已移出黑名单', TOAST_TYPE.SUCCESS)
  removeVisible.value = false
  removeTarget.value = null
  loadBlacklist()
}

function onBlackPageChange(value) {
  blackPage.value = value
  loadBlacklist()
}
function onBlackPageSizeChange(value) {
  blackPageSize.value = value
  blackPage.value = 1
  loadBlacklist()
}

// 切换标签时按需加载对应数据
function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'filter') {
    loadFilters()
  } else {
    loadBlacklist()
  }
}

onMounted(loadFilters)
</script>

<template>
  <div class="panel">
    <p class="panel__desc">配置消息过滤规则与客户黑名单，命中过滤或黑名单的消息将不触发自动回复。</p>

    <!-- 标签切换 -->
    <div class="tabs">
      <button class="tab" :class="{ 'tab--active': activeTab === 'filter' }" type="button" @click="switchTab('filter')">
        过滤规则
      </button>
      <button class="tab" :class="{ 'tab--active': activeTab === 'blacklist' }" type="button" @click="switchTab('blacklist')">
        黑名单
      </button>
    </div>

    <!-- 过滤规则 -->
    <template v-if="activeTab === 'filter'">
      <div class="panel__toolbar">
        <button class="btn btn--primary" type="button" @click="openFilterCreate">新增过滤规则</button>
      </div>
      <div class="block__body">
        <Loading :visible="filterLoading" text="加载中..." />
        <TableContainer max-height="300px">
          <table class="data-table">
            <thead>
              <tr>
                <th>条件类型</th>
                <th>条件值</th>
                <th>状态</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="filterList.length === 0">
                <td colspan="4" class="empty">暂无数据</td>
              </tr>
              <tr v-for="row in filterList" :key="row.id">
                <td>{{ conditionTypeLabel(row.condition_type) }}</td>
                <td><SafeHtml :content="row.condition_value" /></td>
                <td>
                  <span class="status" :class="row.enabled !== false ? 'status--on' : 'status--off'">
                    {{ row.enabled !== false ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="col-actions">
                  <button class="link" type="button" @click="toggleFilterStatus(row)">
                    {{ row.enabled !== false ? '停用' : '启用' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </TableContainer>
        <Pagination
          :page="filterPage"
          :page-size="filterPageSize"
          :total="filterTotal"
          @update:page="onFilterPageChange"
          @update:page-size="onFilterPageSizeChange"
        />
      </div>
    </template>

    <!-- 黑名单 -->
    <template v-else>
      <div class="panel__toolbar">
        <button class="btn btn--primary" type="button" @click="openBlackCreate">加入黑名单</button>
      </div>
      <div class="block__body">
        <Loading :visible="blackLoading" text="加载中..." />
        <TableContainer max-height="300px">
          <table class="data-table">
            <thead>
              <tr>
                <th>客户标识</th>
                <th>状态</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="blackList.length === 0">
                <td colspan="3" class="empty">暂无数据</td>
              </tr>
              <tr v-for="row in blackList" :key="row.id">
                <td><SafeHtml :content="row.customer_uid" /></td>
                <td>
                  <span class="status" :class="row.is_active !== false ? 'status--on' : 'status--off'">
                    {{ row.is_active !== false ? '生效中' : '已移出' }}
                  </span>
                </td>
                <td class="col-actions">
                  <button
                    v-if="row.is_active !== false"
                    class="link link--danger"
                    type="button"
                    @click="openRemove(row)"
                  >
                    移出
                  </button>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </TableContainer>
        <Pagination
          :page="blackPage"
          :page-size="blackPageSize"
          :total="blackTotal"
          @update:page="onBlackPageChange"
          @update:page-size="onBlackPageSizeChange"
        />
      </div>
    </template>

    <!-- 新增过滤规则弹窗 -->
    <FormModal
      v-model="filterModalVisible"
      title="新增过滤规则"
      :loading="filterSaving"
      confirm-text="保存"
      @confirm="submitFilter"
    >
      <div class="modal-form">
        <label class="field">
          <span class="field__label">条件类型</span>
          <Select v-model="filterForm.condition_type" :options="conditionTypeOptions" />
        </label>
        <label class="field">
          <span class="field__label">条件值</span>
          <input v-model="filterForm.condition_value" class="field__input" type="text" placeholder="如关键词、正则表达式或消息类型" />
        </label>
      </div>
    </FormModal>

    <!-- 加入黑名单弹窗 -->
    <FormModal
      v-model="blackModalVisible"
      title="加入黑名单"
      :loading="blackSaving"
      confirm-text="保存"
      @confirm="submitBlack"
    >
      <label class="field">
        <span class="field__label">客户标识 customer_uid</span>
        <input v-model="blackForm.customer_uid" class="field__input" type="text" placeholder="请输入客户唯一标识" />
      </label>
    </FormModal>

    <!-- 移出黑名单确认 -->
    <ConfirmModal
      v-model="removeVisible"
      type="warning"
      title="移出确认"
      message="确认将该客户移出黑名单？移出后将恢复对其自动回复。"
      confirm-text="移出"
      :loading="removing"
      @confirm="confirmRemove"
    />
  </div>
</template>

<style scoped>
@import '@/components/shop_settings/panel.css';

.block__body {
  position: relative;
}
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 12px;
}
.tab {
  padding: 8px 16px;
  font-size: 14px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.tab--active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.muted {
  color: var(--color-text-secondary);
}
.modal-form {
  display: flex;
  flex-direction: column;
}
</style>
