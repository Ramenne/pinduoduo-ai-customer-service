<!--
  商品管理页面（拼多多自动回复系统前端，任务 17.3）
  职责（需求 15）：
    1. 商品列表：按店铺后端分页展示商品（名称、goods_id、价格、销量、缩略图，需求 15.1）；
    2. 商品同步：触发同步从拼多多拉取并 upsert（需求 15.2）；签名缺失等失败由后端统一提示（需求 15.3）；
    3. 从商品记录创建商品专属回复 / 商品知识（需求 15.5）。
  规范遵循：
    - 全中文文案（规范 27）；showToast 由 request 统一提示（规范 6/2）；
    - 表格固定高度内部滚动（规范 29）；加载遮罩 + 转圈（规范 23）；
    - 弹窗仅「关闭」按钮关闭、禁止点遮罩关闭（规范 7，使用 ConfirmModal）；
    - 用户输入经 SafeHtml 转义渲染防范 XSS（规范 22）；后端分页（规范 28）。
-->
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  FormModal,
  Loading,
  Pagination,
  SafeHtml,
  Select,
  TableContainer,
} from '@/components/common'
import { productApi, shopApi } from '@/api'
import { createLatestLoad } from '@/utils/latest_load'
import { watchProductSync } from '@/utils/product_sync_events'
import { showToast, TOAST_TYPE } from '@/utils/toast'

// ====================== 店铺选择 ======================
const shopOptions = ref([])
const selectedShopPk = ref('')

// 加载店铺列表供选择（商品列表强制按店铺查询，需求 15.1）
async function loadShops() {
  const data = await shopApi.fetchShops({ page: 1, page_size: 100, status: 1 })
  const list = (data && data.list) || []
  shopOptions.value = list.map((shop) => ({
    value: shop.id,
    label: shop.shop_name || shop.shop_id || `店铺#${shop.id}`,
  }))
  // 默认选中第一个店铺并加载其商品
  if (shopOptions.value.length > 0) {
    selectedShopPk.value = shopOptions.value[0].value
    await loadProducts()
  }
}

// ====================== 商品列表 ======================
const products = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const listLoading = ref(false)
const listLoader = createLatestLoad(value => { listLoading.value = value })
onUnmounted(() => listLoader.invalidate())

// 加载商品列表（后端分页，需求 15.1）
async function loadProducts() {
  if (!selectedShopPk.value) {
    listLoader.invalidate()
    products.value = []; total.value = 0
    return
  }
  const params = { shop_pk: selectedShopPk.value, page: page.value, page_size: pageSize.value }
  try {
    await listLoader.run(() => productApi.listProducts(params), data => {
      products.value = Array.isArray(data?.list) ? data.list : []
      total.value = data?.total || 0
    })
  } catch { /* request interceptor displays the current failure */ }
}

function onShopChange() {
  products.value = []; total.value = 0
  page.value = 1
  loadProducts()
}

// 分页：页码变化
function onPageChange(p) {
  page.value = p
  loadProducts()
}

// 分页：每页条数变化（组件同时将页码重置为 1）
function onPageSizeChange(size) {
  pageSize.value = size
  page.value = 1
  loadProducts()
}

// ====================== 商品同步（需求 15.2/15.3）======================
const submittingSync = ref(false)
const syncState = ref({ state: 'idle' })
const syncConnectionError = ref('')
const syncing = computed(() => submittingSync.value || ['queued', 'running'].includes(syncState.value.state))
let syncWatcher = null
let syncSequence = 0

function followSync(state, shopPk, sequence) {
  syncState.value = state
  syncConnectionError.value = ''
  if (!['queued', 'running'].includes(state.state)) return
  syncWatcher?.stop()
  syncWatcher = watchProductSync({
    url: `${import.meta.env.VITE_API_BASE_URL || '/api/v1'}/products/sync-events?shop_pk=${encodeURIComponent(shopPk)}`,
    token: () => localStorage.getItem('auth_token'),
    onState(next) {
      if (sequence !== syncSequence || shopPk !== selectedShopPk.value) return
      syncState.value = next
      if (!['queued', 'running'].includes(next.state)) {
        loadProducts()
        if (detailModalOpen.value) loadDetail()
      }
    },
    onUnavailable(message) {
      if (sequence === syncSequence) syncConnectionError.value = `${message}；后台任务继续执行。`
    },
  })
}

async function reconnectSync() {
  const sequence = ++syncSequence
  syncWatcher?.stop()
  const shopPk = selectedShopPk.value
  if (!shopPk) return
  try {
    const state = await productApi.getSyncStatus(shopPk)
    if (sequence !== syncSequence) return
    followSync(state, shopPk, sequence)
  } catch {
    if (sequence === syncSequence) syncConnectionError.value = '无法读取同步状态，可重连；已保存商品仍可查看。'
  }
}

async function submitSync(productId = null) {
  const shopPk = selectedShopPk.value
  if (!shopPk || syncing.value) return
  const sequence = ++syncSequence
  syncWatcher?.stop()
  submittingSync.value = true
  try {
    const state = productId ? await productApi.refreshProduct(productId)
      : await productApi.syncProducts(shopPk)
    if (sequence !== syncSequence) return
    followSync(state, shopPk, sequence)
  } catch {
    // 请求结果未知时查询持久状态，不盲目再建任务。
    if (sequence === syncSequence) {
      submittingSync.value = false
      await reconnectSync()
    }
  }
  finally { if (sequence === syncSequence) submittingSync.value = false }
}
function syncProducts() { return submitSync() }
watch(selectedShopPk, () => {
  ++syncSequence
  syncWatcher?.stop()
  submittingSync.value = false
  syncState.value = { state: 'idle' }
  syncConnectionError.value = ''
  reconnectSync()
})
function onVisible() {
  if (document.visibilityState === 'visible') {
    loadProducts()
    reconnectSync()
  }
}
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onUnmounted(() => {
  ++syncSequence
  syncWatcher?.stop()
  document.removeEventListener('visibilitychange', onVisible)
})

// ====================== 从商品创建商品专属回复 ======================
const replyModalOpen = ref(false)
const replyModalLoading = ref(false)
const replyContent = ref('')
const activeProduct = ref(null)

// 打开创建商品专属回复弹窗
function openReplyModal(product) {
  activeProduct.value = product
  replyContent.value = ''
  replyModalOpen.value = true
}

// 确认创建商品专属回复（需求 15.5）
async function confirmCreateReply() {
  if (!replyContent.value.trim()) {
    showToast('请填写回复内容', TOAST_TYPE.WARNING)
    return
  }
  replyModalLoading.value = true
  try {
    await productApi.createGoodsReplyFromProduct(activeProduct.value.id, {
      reply_content: replyContent.value.trim(),
      reply_type: 'text',
      enabled: true,
    })
    showToast('商品专属回复创建成功', TOAST_TYPE.SUCCESS)
    replyModalOpen.value = false
  } catch (error) {
    // 已由 request 统一提示
  } finally {
    replyModalLoading.value = false
  }
}

// ====================== 从商品创建商品知识 ======================
const knowledgeModalOpen = ref(false)
const knowledgeModalLoading = ref(false)
const knowledgeContent = ref('')

// 打开创建商品知识弹窗
function openKnowledgeModal(product) {
  activeProduct.value = product
  knowledgeContent.value = ''
  knowledgeModalOpen.value = true
}

// 确认创建商品知识（需求 15.5）
async function confirmCreateKnowledge() {
  knowledgeModalLoading.value = true
  try {
    await productApi.createKnowledgeFromProduct(activeProduct.value.id, {
      extracted_content: knowledgeContent.value.trim() || null,
    })
    showToast('商品知识创建成功', TOAST_TYPE.SUCCESS)
    knowledgeModalOpen.value = false
  } catch (error) {
    // 已由 request 统一提示
  } finally {
    knowledgeModalLoading.value = false
  }
}

// 当前选中商品名称（弹窗提示用）
const activeProductName = computed(
  () => (activeProduct.value && activeProduct.value.goods_name) || ''
)

// ====================== 商品详情（需求 15）======================
const detailModalOpen = ref(false)
const detailLoading = ref(false)
const productDetail = ref(null)
const detailProductId = ref(null)

// 先展示当前商品，再读取库内详情；实时规格由用户显式补充。
let detailSequence = 0
async function loadDetail() {
  if (!detailProductId.value) return
  const sequence = ++detailSequence
  const productId = detailProductId.value
  detailLoading.value = true
  try {
    const data = await productApi.getProductDetail(productId)
    if (sequence !== detailSequence || !detailModalOpen.value) return
    productDetail.value = data || productDetail.value
  } catch (error) {
    if (sequence !== detailSequence || !detailModalOpen.value) return
    productDetail.value = { ...productDetail.value,
      detail_message: '补充信息暂未更新，已保留商品信息，请稍后重试。' }
  } finally {
    if (sequence === detailSequence) detailLoading.value = false
  }
}
function openDetailModal(product) {
  ++detailSequence
  detailLoading.value = false
  activeProduct.value = product
  productDetail.value = { ...product, specifications: product.specifications || [], detail_message: '' }
  detailProductId.value = product.id
  detailModalOpen.value = true
  loadDetail()
}
watch(detailModalOpen, visible => {
  if (!visible) { ++detailSequence; detailLoading.value = false }
}, { flush: 'sync' })
watch(selectedShopPk, () => { detailModalOpen.value = false })
onUnmounted(() => { ++detailSequence })

function timeLabel(value) { return value ? value.replace('T', ' ').slice(0, 19) : '尚无成功同步记录' }

// 价格展示（保留两位小数，空值显示 —）
function priceLabel(price) {
  if (price === null || price === undefined) {
    return '—'
  }
  return `¥${Number(price).toFixed(2)}`
}

// 规格摘要：列表中仅展示前两条，其余以「等N项」收起（完整内容见 title / 详情）
function specSummary(specs) {
  if (!Array.isArray(specs) || specs.length === 0) {
    return '—'
  }
  const head = specs.slice(0, 2).join('；')
  return specs.length > 2 ? `${head} 等${specs.length}项` : head
}

onMounted(loadShops)
</script>

<template>
  <div class="product-page">
    <div class="product-page__header">
      <h2 class="product-page__title">商品管理</h2>
      <div class="product-page__actions">
        <div class="product-page__shop">
          <Select
            v-model="selectedShopPk"
            :options="shopOptions"
            placeholder="请选择店铺"
            @change="onShopChange"
          />
        </div>
        <button
          type="button"
          class="btn-sync"
          :disabled="syncing || selectedShopPk === ''"
          @click="syncProducts"
        >
          {{ syncing ? '后台同步中…' : '同步商品' }}
        </button>
      </div>
    </div>
    <p role="status" class="product-detail__tip">
      {{ syncConnectionError || syncState.message || '查看已保存的商品；同步在后台执行。' }}
      <span v-if="syncing && syncState.total">（{{ syncState.completed || 0 }}/{{ syncState.total }}）</span>
      <button v-if="syncConnectionError" type="button" class="btn btn--default" @click="reconnectSync">重新连接</button>
    </p>

    <div class="product-page__body">
      <Loading :visible="listLoading" text="加载中..." />

      <TableContainer max-height="calc(100vh - 260px)">
        <table class="product-table">
          <thead>
            <tr>
              <th class="col-thumb">缩略图</th>
              <th>商品名称</th>
              <th class="col-goods-id">商品ID</th>
              <th class="col-price">价格</th>
              <th class="col-sold">销量</th>
              <th class="col-spec">规格</th>
              <th class="col-op">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="product in products" :key="product.id">
              <td class="col-thumb">
                <img
                  v-if="product.thumb_url"
                  class="product-thumb"
                  :src="product.thumb_url"
                  alt="商品缩略图"
                />
                <span v-else class="product-thumb--empty">无图</span>
              </td>
              <td><SafeHtml :content="product.goods_name || '—'" /><small style="display:block;margin-top:6px" :style="{color:product.platform_on_sale===0?'#b45309':'#64748b'}">{{ product.platform_on_sale===0?'平台未在售':product.platform_on_sale===1?'平台在售':'上架状态待同步' }}</small></td>
              <td class="col-goods-id"><SafeHtml :content="product.goods_id || '—'" /></td>
              <td class="col-price">{{ priceLabel(product.price) }}</td>
              <td class="col-sold">{{ product.sold_quantity != null ? product.sold_quantity : '—' }}</td>
              <td class="col-spec">
                <span
                  v-if="product.specifications && product.specifications.length"
                  class="spec-summary"
                  :title="product.specifications.join('\n')"
                >
                  <SafeHtml :content="specSummary(product.specifications)" />
                </span>
                <span v-else class="spec-empty">—</span>
              </td>
              <td class="col-op">
                <button type="button" class="btn-link" @click="openDetailModal(product)">
                  详情
                </button>
                <button type="button" class="btn-link" @click="openReplyModal(product)">
                  专属回复
                </button>
                <button type="button" class="btn-link" @click="openKnowledgeModal(product)">
                  商品知识
                </button>
              </td>
            </tr>
            <tr v-if="!listLoading && products.length === 0">
              <td class="product-empty" colspan="7">暂无商品数据</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>

      <Pagination
        :page="page"
        :page-size="pageSize"
        :total="total"
        @update:page="onPageChange"
        @update:page-size="onPageSizeChange"
      />
    </div>

    <!-- 商品详情弹窗（仅关闭按钮关闭，规范 7） -->
    <FormModal
      v-model="detailModalOpen"
      title="商品详情"
      :show-footer="false"
    >
      <div class="product-detail__refresh">
        <span role="status">{{ detailLoading ? '读取已保存信息…' : '已保存的商品信息' }}</span>
        <button class="btn btn--default" type="button" :disabled="detailLoading || syncing" @click="submitSync(productDetail?.id)">
          {{ syncing ? '后台更新中…' : '更新规格' }}
        </button>
      </div>
      <div v-if="productDetail" class="product-detail">
        <div class="product-detail__head">
          <img
            v-if="productDetail.thumb_url"
            class="product-detail__thumb"
            :src="productDetail.thumb_url"
            alt="商品缩略图"
          />
          <span v-else class="product-thumb--empty">无图</span>
          <div class="product-detail__title">
            <SafeHtml :content="productDetail.goods_name || '—'" />
          </div>
        </div>
        <dl class="product-detail__list">
          <div class="product-detail__row"><dt>商品同步</dt><dd>{{ timeLabel(productDetail.synced_at) }}</dd></div>
          <div class="product-detail__row"><dt>规格更新</dt><dd>{{ timeLabel(productDetail.specs_updated_at) }}</dd></div>
          <div class="product-detail__row">
            <dt>商品ID</dt>
            <dd><SafeHtml :content="String(productDetail.goods_id || '—')" /></dd>
          </div>
          <div class="product-detail__row">
            <dt>价格</dt>
            <dd>{{ priceLabel(productDetail.price) }}</dd>
          </div>
          <div class="product-detail__row">
            <dt>销量</dt>
            <dd>{{ productDetail.sold_quantity != null ? productDetail.sold_quantity : '—' }}</dd>
          </div>
          <div class="product-detail__row">
            <dt>后台状态</dt>
            <dd>{{ productDetail.status === 1 ? '启用' : '停用' }}</dd>
          </div>
          <div class="product-detail__row product-detail__row--block">
            <dt>规格</dt>
            <dd>
              <ul
                v-if="productDetail.specifications && productDetail.specifications.length"
                class="product-detail__specs"
              >
                <li v-for="(spec, idx) in productDetail.specifications" :key="idx">
                  <SafeHtml :content="spec" />
                </li>
              </ul>
              <span v-else class="product-detail__empty">暂无规格信息</span>
            </dd>
          </div>
        </dl>
        <p v-if="productDetail.detail_message" class="product-detail__tip">
          <SafeHtml :content="productDetail.detail_message" />
        </p>
      </div>
      <p v-else-if="!detailLoading" class="product-detail__empty">暂无详情数据</p>
    </FormModal>

    <!-- 创建商品专属回复弹窗（仅关闭按钮关闭，规范 7） -->
    <FormModal
      v-model="replyModalOpen"
      title="创建商品专属回复"
      confirm-text="保存"
      :loading="replyModalLoading"
      @confirm="confirmCreateReply"
    >
      <p class="modal-form__tip">
        为商品「<SafeHtml :content="activeProductName" />」配置专属回复内容：
      </p>
      <textarea
        v-model="replyContent"
        class="modal-form__input"
        rows="4"
        placeholder="请输入回复内容"
      ></textarea>
    </FormModal>

    <!-- 创建商品知识弹窗（仅关闭按钮关闭，规范 7） -->
    <FormModal
      v-model="knowledgeModalOpen"
      title="创建商品知识"
      confirm-text="保存"
      :loading="knowledgeModalLoading"
      @confirm="confirmCreateKnowledge"
    >
      <p class="modal-form__tip">
        为商品「<SafeHtml :content="activeProductName" />」补充供 AI 检索的知识内容（可选）：
      </p>
      <textarea
        v-model="knowledgeContent"
        class="modal-form__input"
        rows="4"
        placeholder="请输入商品知识内容（如卖点、参数、注意事项）"
      ></textarea>
    </FormModal>
  </div>
</template>

<style scoped>
.product-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.product-page__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.product-page__title {
  font-size: 18px;
  color: var(--color-text);
}

.product-page__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.product-page__shop {
  width: 200px;
}

.btn-sync {
  padding: 8px 18px;
  border: none;
  border-radius: 6px;
  background: var(--color-primary);
  color: var(--color-on-primary);
  font-size: 14px;
  cursor: pointer;
}

.btn-sync:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.product-page__body {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.product-table {
  min-width: 1100px;
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.product-table th,
.product-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text);
}

.product-table thead th {
  background: var(--color-bg-elevated);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.col-thumb {
  width: 80px;
}

.col-goods-id {
  width: 160px;
}

.col-price {
  width: 100px;
}

.col-sold {
  width: 80px;
}

.col-spec {
  width: 200px;
}

.spec-summary {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
  font-size: 13px;
  cursor: default;
}

.spec-empty {
  color: var(--color-text-secondary);
}

.col-op {
  width: 220px;
}

.product-thumb {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border-radius: 4px;
}

.product-thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 4px;
  background: var(--color-hover-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
}

.btn-link {
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
}

.btn-link:hover {
  text-decoration: underline;
}

.product-empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 24px;
}

/* 商品详情弹窗样式 */
.product-detail {
  position: relative;
  min-height: 80px;
}

.product-detail__head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.product-detail__thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
}

.product-detail__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}

.product-detail__list {
  margin: 0;
}

.product-detail__row {
  display: flex;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border);
}

.product-detail__row dt {
  width: 72px;
  flex-shrink: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.product-detail__row dd {
  margin: 0;
  flex: 1;
  color: var(--color-text);
  font-size: 14px;
  word-break: break-all;
}

.product-detail__row--block {
  flex-direction: column;
  gap: 6px;
}

.product-detail__specs {
  margin: 0;
  padding-left: 18px;
}

.product-detail__specs li {
  margin-bottom: 4px;
  font-size: 13px;
  color: var(--color-text);
}

.product-detail__empty {
  color: var(--color-text-secondary);
  font-size: 13px;
}

.product-detail__tip {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--color-hover-bg);
  color: var(--color-text-secondary);
  font-size: 13px;
}

/* 弹窗内表单样式 */
.modal-form__tip {
  font-size: 13px;
  color: var(--color-text);
  margin-bottom: 8px;
}

.modal-form__input {
  width: 100%;
  resize: vertical;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: 14px;
  font-family: inherit;
}

@media (max-width: 767px) {
  .product-page__shop {
    width: 140px;
  }
}
</style>

<style scoped>
.product-detail__refresh{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.product-detail__refresh span{font-size:12px;color:var(--color-text-secondary)}
.product-detail__refresh button{flex-shrink:0}
</style>

<style scoped>
.product-table{min-width:1100px}
.product-table th:nth-child(2),.product-table td:nth-child(2){min-width:240px;overflow-wrap:anywhere}
</style>
