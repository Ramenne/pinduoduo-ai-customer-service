<!--
  客服知识库页面（需求 10.1/10.2/10.6）
  职责：按店铺维度维护客服知识（售后政策、物流、退换货等），支持批量导入：
    - 顶部店铺选择器；表格固定高度内部滚动（规范 29）、后端分页（规范 28）；
    - 新增/编辑经弹窗（仅关闭按钮关闭，规范 7）；
    - 批量导入弹窗：粘贴「标题 || 内容 || 标签」按行解析，跳过同店铺内标题与内容
      完全相同的重复项，返回成功/跳过数量（需求 10.2）；
    - 启用/停用与逻辑删除（禁止物理删除）；加载有遮罩 + 转圈（规范 23）；
    - 用户输入经 SafeHtml 转义展示（规范 22）。
-->
<script setup>
import KnowledgeCuration from '@/components/chat/KnowledgeCuration.vue'
import KnowledgeVersions from '@/components/chat/KnowledgeVersions.vue'
import { onUnmounted, ref, reactive, computed, watch } from 'vue'
import {
  ShopSelector,
  TableContainer,
  Pagination,
  Loading,
  ConfirmModal,
  SafeHtml,
} from '@/components/common'
import { createLatestLoad } from '@/utils/latest_load'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { knowledgeApi } from '@/api'

// 当前选中店铺主键
const shopPk = ref('')

// 列表与分页状态
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const listLoader = createLatestLoad(value => { loading.value = value })
onUnmounted(() => listLoader.invalidate())

// 新增/编辑弹窗
const editVisible = ref(false)
const saving = ref(false)
const isEdit = ref(false)
const editId = ref(null)
const form = reactive({ title: '', content: '', tags: '', enabled: true })

// 批量导入弹窗
const importVisible = ref(false)
const importing = ref(false)
const importText = ref('')

// 删除确认
const deleteVisible = ref(false)
const deleting = ref(false)
const deleteTarget = ref(null)

const editTitle = computed(() => (isEdit.value ? '编辑客服知识' : '新增客服知识'))

// 拉取客服知识列表
async function loadList() {
  if (!shopPk.value) {
    listLoader.invalidate()
    list.value = []; total.value = 0
    return
  }
  const params = { shop_pk: shopPk.value, page: page.value, page_size: pageSize.value }
  try {
    await listLoader.run(() => knowledgeApi.listCsKnowledge(params), data => {
      list.value = Array.isArray(data?.list) ? data.list : []
      total.value = data?.total || 0
    })
  } catch { /* request interceptor displays the current failure */ }
}

function onShopChange() {
  list.value = []; total.value = 0
  page.value = 1
  loadList()
}
function onPageChange(value) {
  page.value = value
  loadList()
}
function onPageSizeChange(value) {
  pageSize.value = value
  page.value = 1
  loadList()
}

// 新增
function openCreate() {
  if (!shopPk.value) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  isEdit.value = false
  editId.value = null
  Object.assign(form, { title: '', content: '', tags: '', enabled: true })
  editVisible.value = true
}

// 编辑
function openEdit(row) {
  isEdit.value = true
  editId.value = row.id
  Object.assign(form, {
    title: row.title || '',
    content: row.content || '',
    tags: row.tags || '',
    enabled: row.enabled !== false,
  })
  editVisible.value = true
}

function closeEdit() {
  editVisible.value = false
}

// 保存（新增/编辑）
async function submitEdit() {
  if (!form.title.trim()) {
    showToast('请填写知识标题', TOAST_TYPE.WARNING)
    return
  }
  if (!form.content.trim()) {
    showToast('请填写知识内容', TOAST_TYPE.WARNING)
    return
  }
  saving.value = true
  let result
  if (isEdit.value) {
    result = await knowledgeApi
      .updateCsKnowledge(editId.value, {
        title: form.title.trim(),
        content: form.content.trim(),
        tags: form.tags || null,
        enabled: form.enabled,
      })
      .catch(() => null)
  } else {
    result = await knowledgeApi
      .createCsKnowledge({
        shop_pk: shopPk.value,
        title: form.title.trim(),
        content: form.content.trim(),
        tags: form.tags || null,
        enabled: form.enabled,
      })
      .catch(() => null)
  }
  saving.value = false
  if (result === null) {
    return
  }
  showToast(isEdit.value ? '保存成功' : '新增成功', TOAST_TYPE.SUCCESS)
  editVisible.value = false
  loadList()
}

// 打开批量导入
function openImport() {
  if (!shopPk.value) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  importText.value = ''
  importVisible.value = true
}

function closeImport() {
  importVisible.value = false
}

// 解析导入文本：每行「标题 || 内容 || 标签(可选)」
function parseImportItems(text) {
  const items = []
  const lines = text.split('\n')
  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      continue
    }
    const parts = line.split('||').map((part) => part.trim())
    const title = parts[0] || ''
    const content = parts[1] || ''
    const tags = parts[2] || ''
    // 标题与内容均必填，缺失则跳过该行
    if (!title || !content) {
      continue
    }
    items.push({ title, content, tags: tags || null, enabled: true })
  }
  return items
}

// 提交批量导入
async function submitImport() {
  const items = parseImportItems(importText.value)
  if (items.length === 0) {
    showToast('未解析到有效条目，请按「标题 || 内容 || 标签」每行一条填写', TOAST_TYPE.WARNING)
    return
  }
  importing.value = true
  const result = await knowledgeApi
    .importCsKnowledge({ shop_pk: shopPk.value, items })
    .catch(() => null)
  importing.value = false
  if (result === null) {
    return
  }
  // 后端返回成功数量与跳过数量（需求 10.2）
  const success = result.success_count ?? result.imported ?? 0
  const skipped = result.skipped_count ?? result.skipped ?? 0
  showToast(`导入完成：成功 ${success} 条，跳过重复 ${skipped} 条`, TOAST_TYPE.SUCCESS)
  importVisible.value = false
  page.value = 1
  loadList()
}

// 启用/停用
async function toggleStatus(row) {
  const nextEnabled = !(row.enabled !== false)
  const result = await knowledgeApi
    .setCsKnowledgeStatus(row.id, nextEnabled)
    .catch(() => null)
  if (result === null) {
    return
  }
  showToast(nextEnabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
  loadList()
}

// 删除
function openDelete(row) {
  deleteTarget.value = row
  deleteVisible.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value) {
    return
  }
  deleting.value = true
  const result = await knowledgeApi.deleteCsKnowledge(deleteTarget.value.id).catch(() => null)
  deleting.value = false
  if (result === null) {
    return
  }
  showToast('已删除', TOAST_TYPE.SUCCESS)
  deleteVisible.value = false
  deleteTarget.value = null
  if (list.value.length === 1 && page.value > 1) {
    page.value -= 1
  }
  loadList()
}

watch(shopPk, (val, oldVal) => {
  if (val && val !== oldVal) {
    onShopChange()
  }
})
</script>

<template>
  <div class="page">
    <h2 class="page__title">客服知识库</h2>
    <p class="page__desc">维护售后政策、物流、退换货等客服知识，支持批量导入，供 AI 回复检索应答。</p>

    <div class="page__toolbar">
      <ShopSelector v-model="shopPk" @change="onShopChange" />
      <button class="btn" type="button" @click="openImport">批量导入</button>
      <button class="btn btn--primary" type="button" @click="openCreate">新增客服知识</button>
    </div>

    <div class="kc-inbox-wrap"><KnowledgeCuration v-if="shopPk" :key="shopPk" :shop-pk="shopPk" category-filter="service" @published="loadList()" /></div>
    <div class="page__table-wrap">
      <Loading :visible="loading" text="加载中..." />
      <TableContainer max-height="100%">
        <table class="data-table">
          <thead>
            <tr>
              <th>标题</th>
              <th>内容</th>
              <th>标签</th>
              <th>状态</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="list.length === 0">
              <td colspan="5" class="empty">暂无数据</td>
            </tr>
            <tr v-for="row in list" :key="row.id">
              <td><SafeHtml :content="row.title" /></td>
              <td class="cell-content"><SafeHtml :content="row.content" /></td>
              <td><SafeHtml :content="row.tags || '-'" /></td>
              <td>
                <span class="status" :class="row.enabled !== false ? 'status--on' : 'status--off'">
                  {{ row.enabled !== false ? '启用' : '停用' }}
                </span>
              </td>
              <td class="col-actions">
                <button class="link" type="button" @click="openEdit(row)">编辑</button>
                <KnowledgeVersions kind="service" :item-id="row.id" @restored="loadList()" />
                <button class="link" type="button" @click="toggleStatus(row)">
                  {{ row.enabled !== false ? '停用' : '启用' }}
                </button>
                <button class="link link--danger" type="button" @click="openDelete(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
    </div>

    <Pagination
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
    />

    <!-- 新增/编辑弹窗 -->
    <transition name="modal-fade">
      <div v-if="editVisible" class="modal-root">
        <div class="modal-overlay"></div>
        <div class="modal-dialog" role="dialog" aria-modal="true">
          <button class="modal-x" type="button" aria-label="关闭" :disabled="saving" @click="closeEdit">✕</button>
          <h3 class="modal-title">{{ editTitle }}</h3>
          <div class="modal-body">
            <label class="field">
              <span class="field__label">标题</span>
              <input v-model="form.title" class="field__input" type="text" placeholder="请输入知识标题" />
            </label>
            <label class="field">
              <span class="field__label">内容</span>
              <textarea v-model="form.content" class="field__textarea" rows="4" placeholder="请输入知识内容"></textarea>
            </label>
            <label class="field">
              <span class="field__label">标签（逗号分隔，可空）</span>
              <input v-model="form.tags" class="field__input" type="text" placeholder="如：售后,退换货" />
            </label>
            <label class="field field--inline">
              <input v-model="form.enabled" type="checkbox" />
              <span class="field__label">启用</span>
            </label>
          </div>
          <div class="modal-footer">
            <button class="btn" type="button" :disabled="saving" @click="closeEdit">取消</button>
            <button class="btn btn--primary" type="button" :disabled="saving" @click="submitEdit">
              {{ saving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </transition>

    <!-- 批量导入弹窗 -->
    <transition name="modal-fade">
      <div v-if="importVisible" class="modal-root">
        <div class="modal-overlay"></div>
        <div class="modal-dialog" role="dialog" aria-modal="true">
          <button class="modal-x" type="button" aria-label="关闭" :disabled="importing" @click="closeImport">✕</button>
          <h3 class="modal-title">批量导入客服知识</h3>
          <div class="modal-body">
            <p class="import-tip">
              每行一条，格式：<strong>标题 || 内容 || 标签</strong>（标签可省略）。
              同店铺内标题与内容完全相同的重复项将自动跳过。
            </p>
            <textarea
              v-model="importText"
              class="field__textarea"
              rows="8"
              placeholder="如：&#10;退货政策 || 7天无理由退货 || 售后&#10;发货时效 || 48小时内发货 || 物流"
            ></textarea>
          </div>
          <div class="modal-footer">
            <button class="btn" type="button" :disabled="importing" @click="closeImport">取消</button>
            <button class="btn btn--primary" type="button" :disabled="importing" @click="submitImport">
              {{ importing ? '导入中...' : '开始导入' }}
            </button>
          </div>
        </div>
      </div>
    </transition>

    <!-- 删除确认 -->
    <ConfirmModal
      v-model="deleteVisible"
      type="danger"
      title="删除确认"
      message="确认删除该客服知识？删除后将不再用于 AI 检索。"
      confirm-text="删除"
      :loading="deleting"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
@import './styles/business_page.css';

/* 内容列限制宽度并省略，避免长文本撑破表格 */
.cell-content {
  max-width: 320px;
}
.cell-content :deep(.safe-html) {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.import-tip {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-bottom: 10px;
  line-height: 1.6;
}
</style>

<style scoped>
.kc-inbox-wrap{flex-shrink:0;max-height:38vh;overflow:auto}
</style>
