<!--
  转人工设置面板（店铺管理「设置」弹窗内嵌，需求 16.1）
  职责：按店铺维度展示可分配人工客服，并维护转人工关键词（新增、列表后端分页、启用/停用）。
  说明：店铺由父级弹窗经 shopPk 传入；新增弹窗仅关闭按钮关闭（规范 7）；
        用户输入经 SafeHtml 转义（规范 22）、文案全中文（规范 27）。
-->
<script setup>
import { reactive, ref, onMounted } from 'vue'
import {
  TableContainer,
  Pagination,
  Loading,
  FormModal,
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

// 客服列表
const csList = ref([])
const csLoading = ref(false)

// 转人工关键词列表与分页
const keywordList = ref([])
const keywordTotal = ref(0)
const keywordPage = ref(1)
const keywordPageSize = ref(20)
const keywordLoading = ref(false)

// 新增关键词弹窗
const modalVisible = ref(false)
const saving = ref(false)
const form = reactive({ keyword: '' })

// 拉取客服列表（需求 16.1）
async function loadCsList() {
  if (!props.shopPk) {
    csList.value = []
    return
  }
  csLoading.value = true
  const data = await autoReplyApi.listCsList(props.shopPk).catch(() => null)
  csLoading.value = false
  if (!data) {
    csList.value = []
    return
  }
  csList.value = Array.isArray(data) ? data : Array.isArray(data.list) ? data.list : []
}

// 拉取转人工关键词列表（后端分页）
async function loadKeywords() {
  if (!props.shopPk) {
    keywordList.value = []
    keywordTotal.value = 0
    return
  }
  keywordLoading.value = true
  const data = await autoReplyApi
    .listTransferKeywords({ shop_pk: props.shopPk, page: keywordPage.value, page_size: keywordPageSize.value })
    .catch(() => null)
  keywordLoading.value = false
  if (!data) {
    return
  }
  keywordList.value = Array.isArray(data.list) ? data.list : []
  keywordTotal.value = data.total || 0
}

function openCreate() {
  if (!props.shopPk) {
    showToast('请先选择店铺', TOAST_TYPE.WARNING)
    return
  }
  form.keyword = ''
  modalVisible.value = true
}

async function submit() {
  if (!form.keyword.trim()) {
    showToast('请填写转人工关键词', TOAST_TYPE.WARNING)
    return
  }
  saving.value = true
  const result = await autoReplyApi
    .createTransferKeyword({ shop_pk: props.shopPk, keyword: form.keyword.trim(), enabled: true })
    .catch(() => null)
  saving.value = false
  if (result === null) {
    return
  }
  showToast('新增成功', TOAST_TYPE.SUCCESS)
  modalVisible.value = false
  keywordPage.value = 1
  loadKeywords()
}

async function toggleStatus(row) {
  const nextEnabled = !(row.enabled !== false)
  const result = await autoReplyApi.setTransferKeywordStatus(row.id, nextEnabled).catch(() => null)
  if (result === null) {
    return
  }
  showToast(nextEnabled ? '已启用' : '已停用', TOAST_TYPE.SUCCESS)
  loadKeywords()
}

function onPageChange(value) {
  keywordPage.value = value
  loadKeywords()
}
function onPageSizeChange(value) {
  keywordPageSize.value = value
  keywordPage.value = 1
  loadKeywords()
}

onMounted(async () => {
  await Promise.all([loadCsList(), loadKeywords()])
})
</script>

<template>
  <div class="panel">
    <p class="panel__desc">查看店铺可分配的人工客服，并配置触发转人工的关键词；命中后将自动转人工并暂停自动回复。</p>

    <!-- 可分配客服列表 -->
    <section class="block">
      <h4 class="block__title">可分配人工客服</h4>
      <div class="cs-wrap">
        <Loading :visible="csLoading" text="加载中..." />
        <div v-if="csList.length === 0 && !csLoading" class="cs-empty">暂无可分配客服</div>
        <ul v-else class="cs-list">
          <li v-for="cs in csList" :key="cs.cs_uid || cs.uid || cs.id" class="cs-item">
            <span class="cs-name"><SafeHtml :content="cs.cs_name || cs.name || '未命名客服'" /></span>
            <span class="cs-uid"><SafeHtml :content="cs.cs_uid || cs.uid || ''" /></span>
          </li>
        </ul>
      </div>
    </section>

    <!-- 转人工关键词 -->
    <section class="block">
      <div class="block__header">
        <h4 class="block__title">转人工关键词</h4>
        <button class="btn btn--primary block__action" type="button" @click="openCreate">新增关键词</button>
      </div>
      <div class="block__body">
        <Loading :visible="keywordLoading" text="加载中..." />
        <TableContainer max-height="280px">
          <table class="data-table">
            <thead>
              <tr>
                <th>关键词</th>
                <th>状态</th>
                <th class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="keywordList.length === 0">
                <td colspan="3" class="empty">暂无数据</td>
              </tr>
              <tr v-for="row in keywordList" :key="row.id">
                <td><SafeHtml :content="row.keyword" /></td>
                <td>
                  <span class="status" :class="row.enabled !== false ? 'status--on' : 'status--off'">
                    {{ row.enabled !== false ? '启用' : '停用' }}
                  </span>
                </td>
                <td class="col-actions">
                  <button class="link" type="button" @click="toggleStatus(row)">
                    {{ row.enabled !== false ? '停用' : '启用' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </TableContainer>
        <Pagination
          :page="keywordPage"
          :page-size="keywordPageSize"
          :total="keywordTotal"
          @update:page="onPageChange"
          @update:page-size="onPageSizeChange"
        />
      </div>
    </section>

    <!-- 新增关键词弹窗 -->
    <FormModal
      v-model="modalVisible"
      title="新增转人工关键词"
      :loading="saving"
      confirm-text="保存"
      @confirm="submit"
    >
      <label class="field">
        <span class="field__label">关键词</span>
        <input v-model="form.keyword" class="field__input" type="text" placeholder="如：人工、投诉、转客服" />
      </label>
    </FormModal>
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
  margin-bottom: 10px;
}
.block__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 10px;
}
.block__action {
  margin-left: auto;
}
.block__body {
  position: relative;
}
.cs-wrap {
  position: relative;
  min-height: 60px;
}
.cs-empty {
  font-size: 13px;
  color: var(--color-text-secondary);
  padding: 12px 0;
}
.cs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  list-style: none;
}
.cs-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 14px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.cs-name {
  font-size: 14px;
  color: var(--color-text);
}
.cs-uid {
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>
