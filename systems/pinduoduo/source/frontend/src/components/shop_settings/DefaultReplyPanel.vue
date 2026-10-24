<!--
  默认与商品专属回复面板（店铺管理「设置」弹窗内嵌，需求 7.1/7.3/7.5）
  职责：按店铺维度配置默认回复（一条），并管理商品专属回复 CRUD（绑定 goods_id，后端分页）。
  说明：店铺由父级弹窗经 shopPk 传入；新增/编辑/删除均经统一弹窗（仅关闭按钮关闭，规范 7）；
        用户输入经 SafeHtml 转义（规范 22）、文案全中文（规范 27）。
-->
<script setup>
import { reactive, ref, onMounted } from 'vue'
import {
  Loading,
  TableContainer,
  Pagination,
  FormModal,
  ConfirmModal,
  SafeHtml,
  Select,
} from '@/components/common'
import { showToast, TOAST_TYPE } from '@/utils/toast'
import { replyApi, productApi } from '@/api'

const props = defineProps({
  shopPk: {
    type: [Number, String],
    default: '',
  },
})

// 回复类型枚举（商品专属回复用）
const REPLY_TYPE_OPTIONS = [
  { value: 'text', label: '文本' },
  { value: 'image', label: '图片' },
]
const REPLY_TYPE_TEXT = { text: '文本', image: '图片' }

// ==================== 默认回复（需求 7.1） ====================
const defaultLoading = ref(false)
const defaultSaving = ref(false)
const defaultReply = reactive({
  content: '',
  enabled: true,
  replyOnce: false,
  exists: false,
})

async function loadDefaultReply() {
  if (!props.shopPk) {
    return
  }
  defaultLoading.value = true
  try {
    const data = await replyApi.fetchDefaultReply(props.shopPk)
    if (data) {
      defaultReply.content = data.content || ''
      defaultReply.enabled = data.enabled !== false
      defaultReply.replyOnce = data.reply_once === true
      defaultReply.exists = true
    } else {
      defaultReply.content = ''
      defaultReply.enabled = true
      defaultReply.replyOnce = false
      defaultReply.exists = false
    }
  } catch (e) {
    // 失败已统一提示
  } finally {
    defaultLoading.value = false
  }
}

async function saveDefaultReply() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  if (!defaultReply.content.trim()) {
    showToast('请填写默认回复内容', TOAST_TYPE.WARNING)
    return
  }
  defaultSaving.value = true
  try {
    await replyApi.saveDefaultReply({
      shop_pk: props.shopPk,
      content: defaultReply.content.trim(),
      enabled: defaultReply.enabled,
      reply_once: defaultReply.replyOnce,
    })
    showToast('默认回复已保存', TOAST_TYPE.SUCCESS)
    await loadDefaultReply()
  } catch (e) {
    // 失败已统一提示
  } finally {
    defaultSaving.value = false
  }
}

// ==================== 商品专属回复（需求 7.3 / 7.5） ====================
const goodsLoading = ref(false)
const goodsReplies = ref([])
const goodsTotal = ref(0)
const goodsPage = ref(1)
const goodsPageSize = ref(20)

async function loadGoodsReplies() {
  if (!props.shopPk) {
    goodsReplies.value = []
    goodsTotal.value = 0
    return
  }
  goodsLoading.value = true
  try {
    const data = await replyApi.fetchGoodsReplies({
      shop_pk: props.shopPk,
      page: goodsPage.value,
      page_size: goodsPageSize.value,
    })
    goodsReplies.value = (data && data.list) || []
    goodsTotal.value = (data && data.total) || 0
  } catch (e) {
    // 失败已统一提示
  } finally {
    goodsLoading.value = false
  }
}

// -------------------- 商品专属回复 新增 / 编辑弹窗 --------------------
const goodsFormVisible = ref(false)
const goodsSaving = ref(false)
const goodsEditing = ref(false)
const editingReplyId = ref(null)
const goodsFormTitle = ref('')

// 商品下拉选项（按当前店铺从商品库查询，需求 15.1）：{ value: goods_id, label: 名称（goods_id） }
const goodsOptions = ref([])
const goodsOptionsLoading = ref(false)

// 加载当前店铺的商品列表用于下拉选择（取较大分页覆盖常见商品数量）
async function loadGoodsOptions() {
  if (!props.shopPk) {
    goodsOptions.value = []
    return
  }
  goodsOptionsLoading.value = true
  try {
    const data = await productApi.listProducts({ shop_pk: props.shopPk, page: 1, page_size: 100 })
    const list = (data && data.list) || []
    goodsOptions.value = list.map((p) => ({
      value: p.goods_id,
      label: p.goods_name ? `${p.goods_name}（${p.goods_id}）` : String(p.goods_id),
    }))
  } catch (e) {
    // 失败已统一提示
    goodsOptions.value = []
  } finally {
    goodsOptionsLoading.value = false
  }
}

const goodsForm = reactive({
  goods_id: '',
  reply_type: 'text',
  reply_content: '',
})

function resetGoodsForm() {
  goodsForm.goods_id = ''
  goodsForm.reply_type = 'text'
  goodsForm.reply_content = ''
  editingReplyId.value = null
}

function openGoodsCreate() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  resetGoodsForm()
  goodsEditing.value = false
  goodsFormTitle.value = '新增商品专属回复'
  goodsFormVisible.value = true
  // 打开新增时按当前店铺加载商品下拉选项
  loadGoodsOptions()
}

function openGoodsEdit(reply) {
  resetGoodsForm()
  goodsEditing.value = true
  editingReplyId.value = reply.id
  goodsFormTitle.value = '编辑商品专属回复'
  goodsForm.goods_id = reply.goods_id
  goodsForm.reply_type = reply.reply_type
  goodsForm.reply_content = reply.reply_content
  goodsFormVisible.value = true
}

function validateGoodsForm() {
  if (!goodsEditing.value && !String(goodsForm.goods_id || '').trim()) {
    showToast('请选择商品', TOAST_TYPE.WARNING)
    return false
  }
  if (!goodsForm.reply_content.trim()) {
    showToast('请填写回复内容', TOAST_TYPE.WARNING)
    return false
  }
  return true
}

async function submitGoodsForm() {
  if (!validateGoodsForm()) {
    return
  }
  goodsSaving.value = true
  try {
    if (goodsEditing.value) {
      await replyApi.updateGoodsReply(editingReplyId.value, {
        reply_type: goodsForm.reply_type,
        reply_content: goodsForm.reply_content.trim(),
      })
      showToast('商品专属回复已更新', TOAST_TYPE.SUCCESS)
    } else {
      await replyApi.createGoodsReply({
        shop_pk: props.shopPk,
        goods_id: String(goodsForm.goods_id).trim(),
        reply_content: goodsForm.reply_content.trim(),
        reply_type: goodsForm.reply_type,
        enabled: true,
      })
      showToast('商品专属回复已创建', TOAST_TYPE.SUCCESS)
      goodsPage.value = 1
    }
    goodsFormVisible.value = false
    await loadGoodsReplies()
  } catch (e) {
    // 失败已统一提示
  } finally {
    goodsSaving.value = false
  }
}

async function toggleGoodsStatus(reply) {
  try {
    await replyApi.setGoodsReplyStatus(reply.id, !reply.enabled)
    showToast(reply.enabled ? '已停用' : '已启用', TOAST_TYPE.SUCCESS)
    await loadGoodsReplies()
  } catch (e) {
    // 失败已统一提示
  }
}

// 删除（逻辑删除）
const goodsDeleteVisible = ref(false)
const goodsDeleting = ref(false)
const goodsDeleteTarget = ref(null)

function openGoodsDelete(reply) {
  goodsDeleteTarget.value = reply
  goodsDeleteVisible.value = true
}

async function confirmGoodsDelete() {
  if (!goodsDeleteTarget.value) {
    return
  }
  goodsDeleting.value = true
  try {
    await replyApi.deleteGoodsReply(goodsDeleteTarget.value.id)
    showToast('商品专属回复已删除', TOAST_TYPE.SUCCESS)
    goodsDeleteVisible.value = false
    await loadGoodsReplies()
  } catch (e) {
    // 失败已统一提示
  } finally {
    goodsDeleting.value = false
  }
}

function onGoodsPageChange(value) {
  goodsPage.value = value
  loadGoodsReplies()
}
function onGoodsPageSizeChange(value) {
  goodsPageSize.value = value
  goodsPage.value = 1
  loadGoodsReplies()
}

onMounted(async () => {
  await Promise.all([loadDefaultReply(), loadGoodsReplies()])
})
</script>

<template>
  <div class="panel">
    <!-- 默认回复（需求 7.1） -->
    <section class="block">
      <div class="block__header">
        <h4 class="block__title">默认回复</h4>
        <span class="block__desc">未命中关键词且未触发 AI 回复时的兜底回复（按店铺一条）。</span>
      </div>
      <div class="block__body">
        <Loading :visible="defaultLoading" text="加载中..." />
        <label class="field">
          <span class="field__label">回复内容</span>
          <textarea
            v-model="defaultReply.content"
            class="field__textarea"
            rows="4"
            placeholder="请输入默认回复内容"
          ></textarea>
        </label>
        <label class="field field--inline">
          <input v-model="defaultReply.enabled" type="checkbox" />
          <span class="field__label">启用默认回复</span>
        </label>
        <label class="field field--inline">
          <input v-model="defaultReply.replyOnce" type="checkbox" />
          <span class="field__label">只回复一次（同一客户仅发送一次默认回复）</span>
        </label>
        <div class="panel__actions">
          <button class="btn btn--primary" type="button" :disabled="defaultSaving || !shopPk" @click="saveDefaultReply">
            {{ defaultSaving ? '保存中...' : '保存默认回复' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 商品专属回复（需求 7.3 / 7.5） -->
    <section class="block">
      <div class="block__header">
        <h4 class="block__title">商品专属回复</h4>
        <span class="block__desc">绑定 goods_id 的回复，优先级高于默认回复。</span>
        <button class="btn btn--primary block__action" type="button" @click="openGoodsCreate">新增</button>
      </div>
      <div class="block__body block__table">
        <Loading :visible="goodsLoading" text="加载中..." />
        <TableContainer max-height="320px">
          <table class="data-table">
            <thead>
              <tr>
                <th>商品标识</th>
                <th>回复类型</th>
                <th>回复内容</th>
                <th>状态</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="reply in goodsReplies" :key="reply.id">
                <td><SafeHtml :content="reply.goods_id" /></td>
                <td>{{ REPLY_TYPE_TEXT[reply.reply_type] || reply.reply_type }}</td>
                <td class="data-table__content"><SafeHtml :content="reply.reply_content" /></td>
                <td>
                  <span class="status" :class="reply.enabled ? 'status--on' : 'status--off'">
                    {{ reply.enabled ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="col-actions">
                  <button class="link" type="button" @click="openGoodsEdit(reply)">编辑</button>
                  <button class="link" type="button" @click="toggleGoodsStatus(reply)">
                    {{ reply.enabled ? '停用' : '启用' }}
                  </button>
                  <button class="link link--danger" type="button" @click="openGoodsDelete(reply)">删除</button>
                </td>
              </tr>
              <tr v-if="!goodsLoading && goodsReplies.length === 0">
                <td class="empty" colspan="5">暂无商品专属回复</td>
              </tr>
            </tbody>
          </table>
        </TableContainer>
        <Pagination
          :total="goodsTotal"
          :page="goodsPage"
          :page-size="goodsPageSize"
          @update:page="onGoodsPageChange"
          @update:page-size="onGoodsPageSizeChange"
        />
      </div>
    </section>

    <!-- 商品专属回复 新增 / 编辑弹窗 -->
    <FormModal
      v-model="goodsFormVisible"
      :title="goodsFormTitle"
      :loading="goodsSaving"
      confirm-text="保存"
      @confirm="submitGoodsForm"
    >
      <div class="modal-form">
        <label v-if="!goodsEditing" class="field">
          <span class="field__label">选择商品（goods_id）</span>
          <Select
            v-model="goodsForm.goods_id"
            :options="goodsOptions"
            :placeholder="goodsOptionsLoading ? '商品加载中...' : (goodsOptions.length ? '请选择商品' : '该店铺暂无商品，请先到商品管理同步')"
          />
        </label>
        <label class="field">
          <span class="field__label">回复类型</span>
          <Select v-model="goodsForm.reply_type" :options="REPLY_TYPE_OPTIONS" />
        </label>
        <label class="field">
          <span class="field__label">回复内容</span>
          <textarea
            v-model="goodsForm.reply_content"
            class="field__textarea"
            rows="4"
            :placeholder="goodsForm.reply_type === 'image' ? '请输入图片地址 URL' : '请输入回复文本'"
          ></textarea>
        </label>
      </div>
    </FormModal>

    <!-- 删除确认 -->
    <ConfirmModal
      v-model="goodsDeleteVisible"
      type="danger"
      title="删除商品专属回复"
      :message="`确认删除商品「${goodsDeleteTarget && goodsDeleteTarget.goods_id}」的专属回复吗？数据将逻辑删除并保留。`"
      confirm-text="确认删除"
      :loading="goodsDeleting"
      @confirm="confirmGoodsDelete"
    />
  </div>
</template>

<style scoped>
@import '@/components/shop_settings/panel.css';

.block {
  margin-bottom: 20px;
}
.block__header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.block__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
.block__desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.block__action {
  margin-left: auto;
}
.block__body {
  position: relative;
}
.modal-form {
  display: flex;
  flex-direction: column;
}
</style>
