<!--
  关键词规则页面（拼多多自动回复系统前端，任务 17.2）
  覆盖需求：
    - 6.1 创建关键词规则（关键词、回复内容、回复类型、优先级；匹配方式后台默认包含）；
    - 6.6 关键词规则列表后端分页；6.7 启用 / 停用规则。
  规范要点：全中文、showToast、弹窗仅关闭按钮关闭、加载遮罩 + 转圈、
           表格固定高度内部滚动、响应式、XSS 防范（SafeHtml）。
-->
<script setup>
import { onUnmounted, ref, reactive, onMounted } from 'vue'
import {
  Loading,
  TableContainer,
  Pagination,
  FormModal,
  ConfirmModal,
  SafeHtml,
  Select,
} from '@/components/common'
import { createLatestLoad } from '@/utils/latest_load'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { keywordApi, shopApi } from '@/api'

// -------------------- 店铺选择（关键词规则按店铺维度配置） --------------------
const shopOptions = ref([]) // 店铺下拉选项
const selectedShopPk = ref('') // 当前选中店铺主键

// 匹配方式：界面不展示，后台统一默认「包含（contains）」
const DEFAULT_MATCH_TYPE = 'contains'

// 回复类型枚举（全中文）
const REPLY_TYPE_OPTIONS = [
  { value: 'text', label: '文本' },
  { value: 'image', label: '图片' },
]
const REPLY_TYPE_TEXT = { text: '文本', image: '图片' }

// 启停用筛选
const enabledFilter = ref('')
const ENABLED_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '停用' },
]

// -------------------- 列表状态 --------------------
const loading = ref(false)
const listLoader = createLatestLoad(value => { loading.value = value })
onUnmounted(() => listLoader.invalidate())
const rules = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 加载店铺下拉（取启用店铺，便于选择）
async function loadShopOptions() {
  try {
    const data = await shopApi.fetchShops({ page: 1, page_size: 100 })
    const list = (data && data.list) || []
    shopOptions.value = list.map((s) => ({
      value: s.id,
      label: s.shop_name ? `${s.shop_name}（${s.shop_id}）` : s.shop_id,
    }))
    // 默认选中第一个店铺并加载其规则
    if (shopOptions.value.length > 0 && selectedShopPk.value === '') {
      selectedShopPk.value = shopOptions.value[0].value
      await loadRules()
    }
  } catch (e) {
    // 失败已统一提示
  }
}

// 加载关键词规则列表（后端分页，需求 6.6）
async function loadRules() {
  if (!selectedShopPk.value) {
    listLoader.invalidate()
    rules.value = []; total.value = 0
    return
  }
  const params = { shop_pk: selectedShopPk.value, page: page.value, page_size: pageSize.value, ...(enabledFilter.value !== '' ? { enabled: enabledFilter.value === 'true' } : {}) }
  try {
    await listLoader.run(() => keywordApi.fetchKeywordRules(params), data => {
      rules.value = Array.isArray(data?.list) ? data.list : []
      total.value = data?.total || 0
    })
  } catch { /* request interceptor displays the current failure */ }
}

function onShopChange() {
  rules.value = []; total.value = 0
  page.value = 1
  loadRules()
}
function onEnabledChange() {
  page.value = 1
  loadRules()
}

// -------------------- 新增 / 编辑弹窗 --------------------
const formVisible = ref(false)
const saving = ref(false)
const isEditing = ref(false)
const editingRuleId = ref(null)
const formTitle = ref('')

const form = reactive({
  keyword: '',
  reply_type: 'text',
  reply_content: '',
  priority: 0,
})

function resetForm() {
  form.keyword = ''
  form.reply_type = 'text'
  form.reply_content = ''
  form.priority = 0
  editingRuleId.value = null
}

// 打开新增弹窗
function openCreate() {
  if (selectedShopPk.value === '') {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  resetForm()
  isEditing.value = false
  formTitle.value = '新增关键词规则'
  formVisible.value = true
}

// 打开编辑弹窗
function openEdit(rule) {
  resetForm()
  isEditing.value = true
  editingRuleId.value = rule.id
  formTitle.value = '编辑关键词规则'
  form.keyword = rule.keyword
  form.reply_type = rule.reply_type
  form.reply_content = rule.reply_content
  form.priority = rule.priority
  formVisible.value = true
}

// 表单校验
function validateForm() {
  if (!form.keyword.trim()) {
    showToast('请填写关键词', TOAST_TYPE.WARNING)
    return false
  }
  if (!form.reply_content.trim()) {
    showToast('请填写回复内容', TOAST_TYPE.WARNING)
    return false
  }
  return true
}

// 提交表单
async function submitForm() {
  if (!validateForm()) {
    return
  }
  saving.value = true
  try {
    if (isEditing.value) {
      await keywordApi.updateKeywordRule(editingRuleId.value, {
        keyword: form.keyword.trim(),
        match_type: DEFAULT_MATCH_TYPE,
        reply_type: form.reply_type,
        reply_content: form.reply_content.trim(),
        priority: Number(form.priority) || 0,
      })
      showToast('规则已更新', TOAST_TYPE.SUCCESS)
    } else {
      await keywordApi.createKeywordRule({
        shop_pk: selectedShopPk.value,
        keyword: form.keyword.trim(),
        match_type: DEFAULT_MATCH_TYPE,
        reply_content: form.reply_content.trim(),
        reply_type: form.reply_type,
        priority: Number(form.priority) || 0,
        enabled: true,
      })
      showToast('规则已创建', TOAST_TYPE.SUCCESS)
      page.value = 1
    }
    formVisible.value = false
    await loadRules()
  } catch (e) {
    // 失败已统一提示
  } finally {
    saving.value = false
  }
}

// -------------------- 启停用 / 删除 --------------------
// 切换启停用（需求 6.7）
async function toggleStatus(rule) {
  try {
    await keywordApi.setKeywordRuleStatus(rule.id, !rule.enabled)
    showToast(rule.enabled ? '规则已停用' : '规则已启用', TOAST_TYPE.SUCCESS)
    await loadRules()
  } catch (e) {
    // 失败已统一提示
  }
}

const deleteVisible = ref(false)
const deleting = ref(false)
const deleteTarget = ref(null)

function openDelete(rule) {
  deleteTarget.value = rule
  deleteVisible.value = true
}

// 确认删除（逻辑删除）
async function confirmDelete() {
  if (!deleteTarget.value) {
    return
  }
  deleting.value = true
  try {
    await keywordApi.deleteKeywordRule(deleteTarget.value.id)
    showToast('规则已删除', TOAST_TYPE.SUCCESS)
    deleteVisible.value = false
    await loadRules()
  } catch (e) {
    // 失败已统一提示
  } finally {
    deleting.value = false
  }
}

onMounted(loadShopOptions)
</script>

<template>
  <div class="kw-page">
    <!-- 操作栏 -->
    <div class="kw-page__toolbar">
      <div class="kw-page__filters">
        <div class="kw-page__filter-item kw-page__filter-item--wide">
          <Select
            v-model="selectedShopPk"
            :options="shopOptions"
            placeholder="请选择店铺"
            @change="onShopChange"
          />
        </div>
        <div class="kw-page__filter-item">
          <Select
            v-model="enabledFilter"
            :options="ENABLED_OPTIONS"
            placeholder="全部状态"
            @change="onEnabledChange"
          />
        </div>
      </div>
      <div class="kw-page__actions">
        <button class="btn btn--primary" type="button" @click="openCreate">新增规则</button>
      </div>
    </div>

    <!-- 列表区 -->
    <div class="kw-page__table-wrap">
      <Loading :visible="loading" text="加载中..." />
      <TableContainer>
        <table class="data-table">
          <thead>
            <tr>
              <th>关键词</th>
              <th>回复类型</th>
              <th>回复内容</th>
              <th>优先级</th>
              <th>状态</th>
              <th class="data-table__op">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="rule in rules" :key="rule.id">
              <td><SafeHtml :content="rule.keyword" /></td>
              <td>{{ REPLY_TYPE_TEXT[rule.reply_type] || rule.reply_type }}</td>
              <td class="data-table__content"><SafeHtml :content="rule.reply_content" /></td>
              <td>{{ rule.priority }}</td>
              <td>
                <span class="tag" :class="rule.enabled ? 'tag--on' : 'tag--off'">
                  {{ rule.enabled ? '启用' : '停用' }}
                </span>
              </td>
              <td class="data-table__op">
                <button class="link-btn" type="button" @click="openEdit(rule)">编辑</button>
                <button class="link-btn" type="button" @click="toggleStatus(rule)">
                  {{ rule.enabled ? '停用' : '启用' }}
                </button>
                <button class="link-btn link-btn--danger" type="button" @click="openDelete(rule)">删除</button>
              </td>
            </tr>
            <tr v-if="!loading && rules.length === 0">
              <td class="data-table__empty" colspan="6">暂无关键词规则</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
    </div>

    <!-- 后端分页 -->
    <Pagination :total="total" v-model:page="page" v-model:page-size="pageSize" @change="loadRules" />

    <!-- 新增 / 编辑弹窗 -->
    <FormModal v-model="formVisible" :title="formTitle" :loading="saving" confirm-text="保存" @confirm="submitForm">
      <div class="form">
        <div class="form__item">
          <label class="form__label">关键词<span class="form__required">*</span></label>
          <input v-model="form.keyword" class="form__input" type="text" placeholder="匹配文本" />
        </div>
        <div class="form__item">
          <label class="form__label">回复类型<span class="form__required">*</span></label>
          <Select v-model="form.reply_type" :options="REPLY_TYPE_OPTIONS" />
        </div>
        <div class="form__item">
          <label class="form__label">回复内容<span class="form__required">*</span></label>
          <textarea
            v-model="form.reply_content"
            class="form__textarea"
            rows="4"
            :placeholder="form.reply_type === 'image' ? '请输入图片地址 URL' : '请输入回复文本'"
          ></textarea>
        </div>
        <div class="form__item">
          <label class="form__label">优先级（数值越大越优先）</label>
          <input v-model="form.priority" class="form__input" type="number" min="0" placeholder="0" />
        </div>
      </div>
    </FormModal>

    <!-- 删除确认 -->
    <ConfirmModal
      v-model="deleteVisible"
      type="danger"
      title="删除关键词规则"
      :message="`确认删除关键词「${deleteTarget && deleteTarget.keyword}」吗？数据将逻辑删除并保留。`"
      confirm-text="确认删除"
      :loading="deleting"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.kw-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.kw-page__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.kw-page__filters {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.kw-page__filter-item {
  width: 160px;
}
.kw-page__filter-item--wide {
  width: 240px;
}

.kw-page__table-wrap {
  position: relative;
  flex: 1;
  min-height: 240px;
}

.btn {
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  border: 1px solid var(--color-border, #e5e6eb);
}
.btn--primary {
  background: var(--color-primary, #1677ff);
  color: var(--color-on-primary, #ffffff);
  border-color: var(--color-primary, #1677ff);
}
.btn--primary:hover {
  background: var(--color-primary-hover, #4096ff);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.data-table th,
.data-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border, #e5e6eb);
  color: var(--color-text, #1f2329);
  white-space: nowrap;
}
.data-table thead th {
  font-weight: 600;
  color: var(--color-text-secondary, #5c6573);
  background: var(--color-bg-elevated, #ffffff);
}
.data-table__content {
  max-width: 280px;
  white-space: normal;
  word-break: break-word;
}
.data-table__op {
  width: 180px;
}
.data-table__empty {
  text-align: center;
  color: var(--color-text-secondary, #5c6573);
  padding: 32px 0;
}

.tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
}
.tag--on {
  background: #f0f9eb;
  color: #237804;
}
.tag--off {
  background: #f2f3f5;
  color: #8c8c8c;
}

.link-btn {
  border: none;
  background: transparent;
  color: var(--color-primary, #1677ff);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
}
.link-btn:hover {
  text-decoration: underline;
}
.link-btn--danger {
  color: #ff4d4f;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.form__item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form__label {
  font-size: 13px;
  color: var(--color-text-secondary, #5c6573);
}
.form__required {
  color: #ff4d4f;
  margin-left: 2px;
}
.form__input,
.form__textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 6px;
  font-size: 14px;
  background: var(--color-bg-elevated, #ffffff);
  color: var(--color-text, #1f2329);
  box-sizing: border-box;
}
.form__input:focus,
.form__textarea:focus {
  outline: none;
  border-color: var(--color-primary, #1677ff);
}
.form__textarea {
  resize: vertical;
}
</style>
