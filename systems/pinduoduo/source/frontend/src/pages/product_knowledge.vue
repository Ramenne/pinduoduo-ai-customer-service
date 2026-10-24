<!--
  商品知识库页面（需求 9.1/9.2/9.3/9.5）
  职责：按店铺维度维护商品知识（供 AI 回复检索）：
    - 顶部店铺选择器 + 商品标识筛选；
    - 表格固定高度内部滚动（规范 29）、后端分页（规范 28）；
    - 新增/编辑经弹窗（仅关闭按钮关闭，规范 7），按 (shop_pk, goods_id) upsert 幂等；
    - 启用/停用与逻辑删除（禁止物理删除，需求 9.5）；
    - 加载有遮罩 + 转圈（规范 23）；用户输入经 SafeHtml 转义展示（规范 22）。
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
// 商品标识筛选
const goodsIdFilter = ref('')

// 列表与分页状态（后端分页，规范 28）
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const listLoader = createLatestLoad(value => { loading.value = value })
onUnmounted(() => listLoader.invalidate())

// 新增/编辑弹窗状态
const editVisible = ref(false)
const saving = ref(false)
const isEdit = ref(false)
const editId = ref(null)
const form = reactive({
  goods_id: '',
  goods_name: '',
  price: '',
  specifications: '',
  extracted_content: '',
})

// 删除确认弹窗状态
const deleteVisible = ref(false)
const deleting = ref(false)
const deleteTarget = ref(null)

// 弹窗标题
const editTitle = computed(() => (isEdit.value ? '编辑商品知识' : '新增商品知识'))

// 拉取商品知识列表（后端分页）
async function loadList() {
  if (!shopPk.value) {
    listLoader.invalidate()
    list.value = []; total.value = 0
    return
  }
  const params = { shop_pk: shopPk.value, page: page.value, page_size: pageSize.value, ...(goodsIdFilter.value.trim() ? { goods_id: goodsIdFilter.value.trim() } : {}) }
  try {
    await listLoader.run(() => knowledgeApi.listProductKnowledge(params), data => {
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

// 查询（按商品标识筛选）
function onSearch() {
  page.value = 1
  loadList()
}

// 分页变化
function onPageChange(value) {
  page.value = value
  loadList()
}
function onPageSizeChange(value) {
  pageSize.value = value
  page.value = 1
  loadList()
}

// 打开新增弹窗
function openCreate() {
  if (!shopPk.value) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  isEdit.value = false
  editId.value = null
  Object.assign(form, {
    goods_id: '',
    goods_name: '',
    price: '',
    specifications: '',
    extracted_content: '',
  })
  editVisible.value = true
}

// 打开编辑弹窗
function openEdit(row) {
  isEdit.value = true
  editId.value = row.id
  Object.assign(form, {
    goods_id: row.goods_id || '',
    goods_name: row.goods_name || '',
    price: row.price ?? '',
    specifications: row.specifications || '',
    extracted_content: row.extracted_content || '',
  })
  editVisible.value = true
}

// 关闭编辑弹窗
function closeEdit() {
  editVisible.value = false
}

// 保存（新增 upsert / 编辑）
async function submitEdit() {
  // 前端基础校验
  if (!isEdit.value && !form.goods_id.trim()) {
    showToast('请填写商品标识 goods_id', TOAST_TYPE.WARNING)
    return
  }
  // 价格为可空数值，填写时校验合法性
  let priceValue = null
  if (form.price !== '' && form.price !== null) {
    priceValue = Number(form.price)
    if (Number.isNaN(priceValue) || priceValue < 0) {
      showToast('价格需为非负数值', TOAST_TYPE.WARNING)
      return
    }
  }
  saving.value = true
  let result
  if (isEdit.value) {
    result = await knowledgeApi
      .updateProductKnowledge(editId.value, {
        goods_name: form.goods_name || null,
        price: priceValue,
        specifications: form.specifications || null,
        extracted_content: form.extracted_content || null,
      })
      .catch(() => null)
  } else {
    result = await knowledgeApi
      .upsertProductKnowledge({
        shop_pk: shopPk.value,
        goods_id: form.goods_id.trim(),
        goods_name: form.goods_name || null,
        price: priceValue,
        specifications: form.specifications || null,
        extracted_content: form.extracted_content || null,
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

// 切换启用 / 停用
async function toggleStatus(row) {
  const nextStatus = row.status === 1 ? 0 : 1
  const result = await knowledgeApi
    .setProductKnowledgeStatus(row.id, nextStatus)
    .catch(() => null)
  if (result === null) {
    return
  }
  showToast(nextStatus === 1 ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
  loadList()
}

// 打开删除确认
function openDelete(row) {
  deleteTarget.value = row
  deleteVisible.value = true
}

// 确认逻辑删除
async function confirmDelete() {
  if (!deleteTarget.value) {
    return
  }
  deleting.value = true
  const result = await knowledgeApi
    .deleteProductKnowledge(deleteTarget.value.id)
    .catch(() => null)
  deleting.value = false
  if (result === null) {
    return
  }
  showToast('已删除', TOAST_TYPE.SUCCESS)
  deleteVisible.value = false
  deleteTarget.value = null
  // 删除后当前页可能为空，回退一页避免空白
  if (list.value.length === 1 && page.value > 1) {
    page.value -= 1
  }
  loadList()
}

// 店铺选择器抛出初始值时也会触发，统一交由 onShopChange
watch(shopPk, (val, oldVal) => {
  if (val && val !== oldVal) {
    onShopChange()
  }
})
</script>

<template>
  <div class="page">
    <h2 class="page__title">商品知识库</h2>
    <p class="page__desc">维护商品信息（名称、价格、规格、提取内容），供 AI 回复检索应答。</p>

    <!-- 操作栏：店铺选择 + 筛选 + 新增 -->
    <div class="page__toolbar">
      <ShopSelector v-model="shopPk" @change="onShopChange" />
      <input
        v-model="goodsIdFilter"
        class="page__input"
        type="text"
        placeholder="按商品标识 goods_id 筛选"
        @keyup.enter="onSearch"
      />
      <button class="btn" type="button" @click="onSearch">查询</button>
      <button class="btn btn--primary" type="button" @click="openCreate">新增商品知识</button>
    </div>

    <!-- 表格区（固定高度内部滚动 + 区域加载遮罩） -->
    <div class="kc-inbox-wrap"><KnowledgeCuration v-if="shopPk" :key="shopPk" :shop-pk="shopPk" category-filter="product" @published="loadList()" /></div>
    <div class="page__table-wrap">
      <Loading :visible="loading" text="加载中..." />
      <TableContainer max-height="100%">
        <table class="data-table">
          <thead>
            <tr>
              <th>商品标识</th>
              <th>商品名称</th>
              <th>价格</th>
              <th>状态</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="list.length === 0">
              <td colspan="5" class="empty">暂无数据</td>
            </tr>
            <tr v-for="row in list" :key="row.id">
              <td><SafeHtml :content="row.goods_id" /></td>
              <td><SafeHtml :content="row.goods_name || '-'" /></td>
              <td>{{ row.price ?? '-' }}</td>
              <td>
                <span class="status" :class="row.status === 1 ? 'status--on' : 'status--off'">
                  {{ row.status === 1 ? '启用' : '停用' }}
                </span>
              </td>
              <td class="col-actions">
                <button class="link" type="button" @click="openEdit(row)">编辑</button>
                <KnowledgeVersions kind="product" :item-id="row.id" @restored="loadList()" />
                <button class="link" type="button" @click="toggleStatus(row)">
                  {{ row.status === 1 ? '停用' : '启用' }}
                </button>
                <button class="link link--danger" type="button" @click="openDelete(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
    </div>

    <!-- 分页（后端分页） -->
    <Pagination
      :page="page"
      :page-size="pageSize"
      :total="total"
      @update:page="onPageChange"
      @update:page-size="onPageSizeChange"
    />

    <!-- 新增/编辑弹窗（仅关闭按钮关闭） -->
    <transition name="modal-fade">
      <div v-if="editVisible" class="modal-root">
        <div class="modal-overlay"></div>
        <div class="modal-dialog" role="dialog" aria-modal="true">
          <button class="modal-x" type="button" aria-label="关闭" :disabled="saving" @click="closeEdit">✕</button>
          <h3 class="modal-title">{{ editTitle }}</h3>
          <div class="modal-body">
            <label class="field">
              <span class="field__label">商品标识 goods_id</span>
              <input v-model="form.goods_id" class="field__input" type="text" :disabled="isEdit" placeholder="请输入商品标识" />
            </label>
            <label class="field">
              <span class="field__label">商品名称</span>
              <input v-model="form.goods_name" class="field__input" type="text" placeholder="请输入商品名称" />
            </label>
            <label class="field">
              <span class="field__label">价格</span>
              <input v-model="form.price" class="field__input" type="number" min="0" step="0.01" placeholder="请输入价格（可空）" />
            </label>
            <label class="field">
              <span class="field__label">商品规格</span>
              <textarea v-model="form.specifications" class="field__textarea" rows="2" placeholder="规格说明（可空）"></textarea>
            </label>
            <label class="field">
              <span class="field__label">提取内容（供 AI 检索）</span>
              <textarea v-model="form.extracted_content" class="field__textarea" rows="3" placeholder="供 AI 检索的商品知识内容（可空）"></textarea>
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

    <!-- 删除确认弹窗 -->
    <ConfirmModal
      v-model="deleteVisible"
      type="danger"
      title="删除确认"
      message="确认删除该商品知识？删除后将不再用于 AI 检索。"
      confirm-text="删除"
      :loading="deleting"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
@import './styles/business_page.css';
</style>

<style scoped>
.kc-inbox-wrap{flex-shrink:0;max-height:38vh;overflow:auto}
</style>
