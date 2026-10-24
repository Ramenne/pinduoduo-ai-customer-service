<!--
  店铺管理页面（拼多多自动回复系统前端，任务 17.2）
  覆盖需求：
    - 3.1 新增店铺（账号密码登录 / Cookie 导入两种入口）；
    - 3.3 店铺列表后端分页（默认 20 条，可选 10/20/50/100）；
    - 3.4 修改备注 / 启停用；3.5 停用断连（逻辑删除）；
    - 4.1 账号密码登录入口；4.3 手动粘贴 Cookie 导入入口。
  规范要点：全中文、showToast 由请求层统一处理、弹窗仅关闭按钮关闭、
           加载遮罩 + 转圈、表格固定高度内部滚动、响应式、XSS 防范（SafeHtml）。
-->
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
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
import { formatDateTime } from '@/utils/format'
import ShopSettingsModal from '@/components/shop_settings/ShopSettingsModal.vue'
import QrVerificationWindow from '@/components/shop_settings/QrVerificationWindow.vue'
import SmsVerificationForm from '@/components/shop_settings/SmsVerificationForm.vue'
import { shopApi } from '@/api'
import { fetchPlatformAccounts } from '@/api/platform_api'

const route = useRoute()
const platformCode = computed(() => route.query.platform ?? 'pdd')
const platformSupported = computed(() => platformCode.value === 'pdd')
let listSequence = 0, listDisposed = false

// -------------------- 列表状态 --------------------
const loading = ref(false) // 列表加载遮罩
const shops = ref([]) // 当前页店铺列表
const total = ref(0) // 总记录数
const page = ref(1) // 当前页码
const pageSize = ref(20) // 每页条数（默认 20）
const shopQuery = ref(''), onlineFilter = ref(''), aiFilter = ref('')
const statusFilter = ref('') // 状态筛选：''=全部，1=启用，0=停用

// 状态筛选可选项（全中文）
const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 1, label: '启用' },
  { value: 0, label: '停用' },
]

// 加载店铺列表（后端分页，需求 3.3）
async function loadShops() {
  const sequence = ++listSequence
  if (!platformSupported.value) { shops.value = []; total.value = 0; loading.value = false; return }
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (statusFilter.value !== '') {
      params.status = statusFilter.value
    }
    params.q = shopQuery.value
    if (onlineFilter.value !== '') params.connected = onlineFilter.value
    if (aiFilter.value !== '') params.ai_enabled = aiFilter.value
    const data = await fetchPlatformAccounts(platformCode.value, params)
    if (listDisposed || sequence !== listSequence) return
    // 后端统一分页结构 {list, total, page, page_size}
    shops.value = (data && data.list) || []
    total.value = (data && data.total) || 0
  } catch (e) {
    // 失败已由请求拦截器统一 showToast，此处不再二次提示（规范 2）
  } finally {
    if (sequence === listSequence) loading.value = false
  }
}

// 导出只包含列表信息；跨页读取沿用服务端的权限与当前筛选。
const exporting = ref(false)
async function exportShopList() {
  if (exporting.value) return
  exporting.value = true
  try {
    const rows = []; let next = 1, totalRows = 1
    while (rows.length < totalRows) {
      const params = {page:next++,page_size:100,q:shopQuery.value}
      if(statusFilter.value!=='') params.status=statusFilter.value
      if(onlineFilter.value!=='') params.connected=onlineFilter.value
      if(aiFilter.value!=='') params.ai_enabled=aiFilter.value
      const data=await shopApi.fetchShopOverview(params); totalRows=data.total
      if(!data.list.length) break
      rows.push(...data.list)
    }
    const escape=(v)=>'"'+String(v??'').replace(/^[=+@-]/,"'$&").replaceAll('"','""')+'"'
    const table=[['店铺标识','店铺名称','所属成员','启用状态','消息通道','AI状态','模型','今日记录回复','最近平台同步'],...rows.map(s=>[s.shop_id,s.shop_name,s.owner,s.status===1?'启用':'停用',s.connected?'已连接':'未连接',s.ai_ready?'已就绪':s.ai_enabled?'待配置':'未启用',s.model_name,s.today_replies,s.sync?.last_success_at||'尚未同步'])]
    const url=URL.createObjectURL(new Blob(['\uFEFF'+table.map(row=>row.map(escape).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}))
    const anchor=document.createElement('a');anchor.href=url;anchor.download='店铺列表.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  } finally { exporting.value=false }
}

// 切换筛选：回到第 1 页重新加载
function onStatusChange() {
  page.value = 1
  loadShops()
}

// -------------------- 新增 / 编辑弹窗 --------------------
const formVisible = ref(false)
const saving = ref(false)
const formMode = ref('password') // password=账号密码登录，cookie=Cookie 导入，edit=编辑
const editingShopPk = ref(null)
// 新增与编辑共用的密码显示状态，每次打开弹窗恢复隐藏。
const showPassword = ref(false)
// 编辑模式下店铺详情（含凭据）是否已成功拉取（避免拉取失败时误清空凭据）
const editDetailLoaded = ref(false)

// 表单数据模型
const form = reactive({
  shop_id: '',
  shop_name: '',
  remark: '',
  username: '',
  password: '',
  cookies: '',
})

// 弹窗标题（按模式区分）
const formTitle = ref('')

// 重置表单
function resetForm() {
  showPassword.value = false
  form.shop_id = ''
  form.shop_name = ''
  form.remark = ''
  form.username = ''
  form.password = ''
  form.cookies = ''
  editingShopPk.value = null
}

// 打开「账号密码登录」新增（需求 4.1）
function openPasswordLogin() {
  resetForm()
  formMode.value = 'password'
  formTitle.value = '账号密码登录新增店铺'
  formVisible.value = true
}

// 打开「Cookie 导入」新增（需求 4.3）
function openCookieImport() {
  resetForm()
  formMode.value = 'cookie'
  formTitle.value = 'Cookie 导入新增店铺'
  formVisible.value = true
}

// 打开「编辑」弹窗（需求 3.4 / 3.6）：可改备注 / 名称，并反显账号 / 密码 / Cookie 供修改
async function openEdit(shop) {
  resetForm()
  formMode.value = 'edit'
  formTitle.value = '编辑店铺'
  editingShopPk.value = shop.id
  form.shop_id = shop.shop_id
  form.shop_name = shop.shop_name || ''
  form.remark = shop.remark || ''
  showPassword.value = false
  editDetailLoaded.value = false
  formVisible.value = true
  // 拉取详情以反显账号 / 密码 / Cookie 明文（前端以隐藏查看展示，支持编辑保存）
  const detail = await shopApi.fetchShopDetail(shop.id).catch(() => null)
  if (detail) {
    form.username = detail.username || ''
    form.password = detail.password || ''
    form.cookies = detail.cookies || ''
    editDetailLoaded.value = true
  }
}

// 前端基础校验（必填项）；返回 true 表示校验通过
function validateForm() {
  if (formMode.value === 'edit') {
    return true
  }
  if (formMode.value === 'password') {
    if (!form.username.trim() || !form.password.trim()) {
      showToast('请填写拼多多登录账号与密码', TOAST_TYPE.WARNING)
      return false
    }
  }
  if (formMode.value === 'cookie') {
    if (!form.cookies.trim()) {
      showToast('请粘贴有效的 Cookie 文本', TOAST_TYPE.WARNING)
      return false
    }
  }
  return true
}

// 提交表单（按模式调用不同接口）
async function submitForm() {
  if (saving.value) return
  if (!validateForm()) {
    return
  }
  saving.value = true
  try {
    if (formMode.value === 'edit') {
      const payload = {
        shop_name: form.shop_name.trim() || undefined,
        remark: form.remark.trim() || undefined,
      }
      // 仅在详情成功拉取（凭据已反显）时回传凭据，避免拉取失败误清空已存凭据。
      // 反显编辑为「所见即所得」：提交当前展示值（留空即清空对应凭据）。
      if (editDetailLoaded.value) {
        payload.username = form.username.trim()
        payload.password = form.password
        payload.cookies = form.cookies.trim()
      }
      await shopApi.updateShop(editingShopPk.value, payload)
      showToast('店铺已更新', TOAST_TYPE.SUCCESS)
    } else if (formMode.value === 'password') {
      await startPasswordLoginSession({
        username: form.username.trim(),
        password: form.password,
        remark: form.remark.trim() || undefined,
      })
      form.password = ''
      return
    } else if (formMode.value === 'cookie') {
      await shopApi.importShopByCookie({
        cookies: form.cookies.trim(),
        remark: form.remark.trim() || undefined,
      })
      showToast('Cookie 导入成功，已自动获取店铺信息', TOAST_TYPE.SUCCESS)
    } else {
      // 扫码登录走独立弹窗与流程，不经过本提交函数；此处兜底防御模式串台。
      showToast('当前模式不支持该操作，请重新打开弹窗', TOAST_TYPE.WARNING)
    }
    formVisible.value = false
    // 新增后回到第 1 页，编辑保持当前页
    if (formMode.value !== 'edit') {
      page.value = 1
    }
    await loadShops()
  } catch (e) {
    // 失败已统一提示
  } finally {
    saving.value = false
  }
}

// -------------------- 扫码登录（本轮新增） --------------------
// 设计要点：
//  - 二维码由服务器侧一次性浏览器取回、经 backend 以 base64 转发，前端只展示图片，
//    永远拿不到 Cookie；「绑定成功」只在身份核验通过且落库成功后才出现。
//  - 轮询必须可清理：关闭弹窗、切换页面（组件卸载）都要停表并取消会话，
//    否则服务器上的浏览器会一直占着并发额度直到超时。
const QR_POLL_INTERVAL_MS = 2000
const QR_FIRST_IMAGE_POLL_INTERVAL_MS = 500

// 状态中文文案（与 websocket 侧状态枚举一一对应，不得合并成笼统的"失败"）。
const QR_STATE_TEXT = {
  waiting: '等待扫码：请用拼多多商家版 App 扫描二维码',
  scanned: '已扫码，请在手机上确认登录',
  verification_required:
    '平台要求先完成一次人工安全验证。系统不会代您通过验证码，请点击下方按钮由您本人完成。',
  confirmed: '确认成功，正在核验店铺身份并保存',
  claimed: '凭据已保存',
  expired: '登录二维码已失效',
  cancelled: '扫码会话已取消',
  failed: '扫码登录未完成',
}

const qrMode = ref('qr')
const qrMessage = ref('')
const qrVerification = ref(null)
const qrVisible = ref(false)
const qrSessionId = ref('')
const qrImage = ref('')
const qrState = ref('waiting')
const qrTerminal = ref(false)
const qrTargetShopId = ref('')
const qrError = ref('')
const qrBinding = ref(false)
const qrOpening = ref(false)
// 人工验证窗口**默认不展开**：正常扫码只展示二维码本身，不自动回退成完整浏览器页面。
// 仅当平台真的要求人工验证、且用户显式点击「开始人工验证」后才打开该窗口。
const qrVerifyOpen = ref(false)
let qrTimer = null
// 轮询重入保护：单次请求超过轮询间隔时避免并发叠加。
let qrPolling = false
let qrNextPollAt = 0
// 创建会话时 websocket 返回的进程号，用于识别多 worker 部署导致的跨进程查询。
let qrWorkerPid = null

function qrStatusText() {
  if (qrError.value) return qrError.value
  if (qrMode.value === 'password') return qrMessage.value || '正在处理账号登录…'
  return QR_STATE_TEXT[qrState.value] || '正在处理…'
}

function stopQrPolling() {
  if (qrTimer !== null) {
    clearInterval(qrTimer)
    qrTimer = null
  }
}

function startQrPolling() {
  stopQrPolling()
  if (qrTerminal.value || !qrSessionId.value) return
  qrNextPollAt = 0
  void pollQrSession()
  qrTimer = setInterval(pollQrSession, QR_FIRST_IMAGE_POLL_INTERVAL_MS)
}

// 应用一次会话视图：更新图片与状态，terminal 为真时停表。
function applyQrView(data) {
  qrState.value = data.state || qrState.value
  qrMessage.value = data.message || ''
  qrVerification.value = data.verification || null
  qrTerminal.value = !!data.terminal
  if (data.qr_image_base64) {
    qrImage.value = `data:image/png;base64,${data.qr_image_base64}`
  } else if (qrState.value !== 'waiting') {
    qrImage.value = ''
  }
  // 一旦不再处于"需要人工验证"，就收起验证窗口：让用户回到二维码主视图继续扫码。
  if (qrMode.value !== 'password' && qrState.value !== 'verification_required') {
    qrVerifyOpen.value = false
  }
  // 多 worker 部署时轮询可能打到另一个进程，那里没有这个会话；给出准确原因。
  if (qrWorkerPid !== null && data.worker_pid && String(data.worker_pid) !== String(qrWorkerPid)) {
    qrError.value = '扫码服务以多进程方式运行，会话无法跨进程查询。请将 websocket 服务改为单进程部署。'
    qrTerminal.value = true
    stopQrPolling()
    return
  }
  if (data.message && qrState.value === 'verification_required') {
    // 需要人工验证时把服务器给的说明原样呈现（含"在哪里操作"），但不当作错误终止。
    qrError.value = ''
  }
  if (qrTerminal.value && ['expired', 'cancelled', 'failed'].includes(qrState.value)) {
    qrError.value = data.message || QR_STATE_TEXT[qrState.value]
    stopQrPolling()
  }
}

// 打开扫码弹窗；传入 shop 时为「为该店铺重新授权」，后端会把 target_shop_id 绑进会话，
// 扫码确认到别的店铺会被拒绝且不动原凭据。
async function openQrLogin(shop = null) {
  if (qrOpening.value) return
  qrOpening.value = true
  qrMode.value = 'qr'
  qrVerification.value = null
  qrMessage.value = ''
  stopQrPolling()
  qrError.value = ''
  qrState.value = 'waiting'
  qrTerminal.value = false
  qrImage.value = ''
  qrBinding.value = false
  qrVerifyOpen.value = false
  qrWorkerPid = null
  qrTargetShopId.value = shop ? String(shop.shop_id) : ''
  try {
    const data = await shopApi.createQrSession(
      qrTargetShopId.value ? { target_shop_id: qrTargetShopId.value } : {}
    )
    qrSessionId.value = data.session_id
    qrWorkerPid = data.worker_pid || null
    applyQrView(data)
    qrVisible.value = true
    startQrPolling()
  } catch (e) {
    // 失败提示已由请求层统一 showToast，这里只把原因留在弹窗内供回看。
    qrError.value = (e && e.message) || '扫码会话创建失败'
    qrTerminal.value = true
    qrVisible.value = true
  } finally {
    qrOpening.value = false
  }
}

async function startPasswordLoginSession(payload) {
  if (qrSessionId.value) await closeQrLogin()
  stopQrPolling()
  qrMode.value = 'password'
  qrVerification.value = null
  qrMessage.value = '正在创建登录会话'
  qrError.value = ''
  qrState.value = 'waiting'
  qrTerminal.value = false
  qrBinding.value = false
  qrImage.value = ''
  qrVerifyOpen.value = false
  qrTargetShopId.value = ''
  const data = await shopApi.createPasswordSession(payload)
  qrSessionId.value = data.session_id
  qrWorkerPid = data.worker_pid || null
  applyQrView(data)
  formVisible.value = false
  qrVisible.value = true
  startQrPolling()
}

// 轮询会话状态；confirmed 时触发一次绑定。terminal 为真时停表。
async function pollQrSession() {
  if (!qrSessionId.value || qrBinding.value || qrPolling || qrTerminal.value) return
  if (Date.now() < qrNextPollAt) return
  const waitingForFirstImage = !qrImage.value && qrState.value === 'waiting'
  qrNextPollAt = Date.now() + (waitingForFirstImage
    ? QR_FIRST_IMAGE_POLL_INTERVAL_MS : QR_POLL_INTERVAL_MS)
  qrPolling = true
  try {
    const data = await shopApi.getQrSession(qrSessionId.value)
    applyQrView(data)
    if (data.state === 'confirmed') {
      await bindQrSession()
    }
  } catch (e) {
    // 会话不存在（40400，含服务重启丢会话）等：终止轮询并展示原因。
    qrError.value = (e && e.message) || '扫码状态查询失败'
    qrTerminal.value = true
    stopQrPolling()
  } finally {
    qrPolling = false
  }
}

// 领取并落库：只有这一步成功才允许显示「绑定成功」。
async function bindQrSession() {
  if (qrBinding.value) return
  qrBinding.value = true
  stopQrPolling()
  try {
    await shopApi.bindQrSession(qrSessionId.value)
    qrState.value = 'confirmed'
    qrTerminal.value = true
    showToast(qrMode.value === 'password' ? '账号密码登录成功，店铺已绑定' : '扫码登录成功，店铺已绑定', TOAST_TYPE.SUCCESS)
    qrSessionId.value = ''
    qrVisible.value = false
    page.value = 1
    await loadShops()
  } catch (e) {
    // 42606：扫码店铺与目标店铺不一致，后端已保证未改动原凭据。
    qrState.value = 'failed'
    qrTerminal.value = true
    qrError.value = (e && e.message) || '绑定失败，原授权未被更改'
  } finally {
    qrBinding.value = false
  }
}

// 关闭弹窗：停表并尽力取消仍在进行中的会话，释放服务器浏览器资源。
async function closeQrLogin() {
  stopQrPolling()
  const pending = qrSessionId.value && !qrTerminal.value
  const sessionId = qrSessionId.value
  qrSessionId.value = ''
  qrImage.value = ''
  qrVerification.value = null
  qrVerifyOpen.value = false
  qrVisible.value = false
  if (pending) {
    await shopApi.cancelQrSession(sessionId, { silentOnFail: true })
  }
}

// 二维码失效时重新发起（旧会话已终态，直接开新的即可）。
async function refreshQrLogin() {
  if (qrSessionId.value) {
    await shopApi.cancelQrSession(qrSessionId.value, { silentOnFail: true })
  }
  stopQrPolling()
  await openQrLogin(qrTargetShopId.value ? { shop_id: qrTargetShopId.value } : null)
}

// 组件卸载（切换到其他菜单）时清理轮询，避免离开页面后仍在打接口。
onUnmounted(() => {
  closeQrLogin()
})

// -------------------- 停用确认 --------------------
const disableVisible = ref(false)
const disabling = ref(false)
const disableTarget = ref(null)

// 打开停用确认（需求 3.5）
function openDisable(shop) {
  disableTarget.value = shop
  disableVisible.value = true
}

// 确认停用：逻辑删除并断连
async function confirmDisable() {
  if (!disableTarget.value) {
    return
  }
  disabling.value = true
  try {
    await shopApi.disableShop(disableTarget.value.id)
    showToast('店铺已停用并断开连接', TOAST_TYPE.SUCCESS)
    disableVisible.value = false
    await loadShops()
  } catch (e) {
    // 失败已统一提示
  } finally {
    disabling.value = false
  }
}

// 启用店铺（需求 3.4）
async function enableShop(shop) {
  try {
    await shopApi.updateShop(shop.id, { enabled: true })
    showToast('店铺已启用', TOAST_TYPE.SUCCESS)
    await loadShops()
  } catch (e) {
    // 失败已统一提示
  }
}

// -------------------- 店铺设置弹窗 --------------------
// 将原本独立菜单的店铺级设置（默认回复 / AI / 营业时间 / 过滤黑名单 / 风控 / 转人工）
// 统一收敛到本页行内「设置」入口，不再单独开菜单。
const settingsVisible = ref(false)
const settingsShop = ref(null)

// 打开某店铺的设置弹窗
function openSettings(shop) {
  settingsShop.value = shop
  settingsVisible.value = true
}

// 状态文案
function statusText(status) {
  return status === 1 ? '启用' : '停用'
}

onMounted(loadShops)
watch(platformCode, () => { page.value = 1; shops.value = []; total.value = 0; loadShops() })
onUnmounted(() => { listDisposed = true; listSequence++ })
</script>

<template>
  <section v-if="!platformSupported" class="platform-unsupported" role="alert">
    <h1>此平台尚未接入</h1><p>请从“平台与账号”选择已接入的平台，不会显示其他平台的账号。</p>
    <RouterLink to="/platforms">返回平台与账号</RouterLink>
  </section>
  <div v-else class="shop-page">
    <header class="shop-platform-heading"><div><h1>拼多多账号</h1><p>服务器登录与店铺授权</p></div><RouterLink to="/platforms">切换平台</RouterLink></header>
    <!-- 操作栏 -->
    <div class="shop-page__toolbar">
      <div class="shop-page__filters">
        <form class="shop-overview-search" @submit.prevent="onStatusChange"><input v-model="shopQuery" placeholder="店铺名称、ID、所属成员" aria-label="搜索店铺"><button class="btn btn--default">查询</button></form>
        <div class="shop-page__filter-item"><Select v-model="onlineFilter" :options="[{value:'',label:'全部消息通道'},{value:true,label:'通道已连接'},{value:false,label:'通道未连接'}]" @change="onStatusChange" /></div>
        <div class="shop-page__filter-item"><Select v-model="aiFilter" :options="[{value:'',label:'全部 AI 状态'},{value:true,label:'AI 已启用'},{value:false,label:'AI 未启用'}]" @change="onStatusChange" /></div>
        <div class="shop-page__filter-item">
          <Select
            v-model="statusFilter"
            :options="STATUS_OPTIONS"
            placeholder="全部状态"
            @change="onStatusChange"
          />
        </div>
      </div>
      <div class="shop-page__actions"><button class="btn btn--default" :disabled="exporting" @click="exportShopList">{{ exporting ? '导出中…' : '导出筛选列表' }}</button><button class="btn btn--default" :disabled="loading" @click="loadShops">刷新状态</button>
        <button class="btn btn--primary" type="button" @click="openPasswordLogin">账号密码登录</button>
        <button class="btn btn--default" type="button" :disabled="qrOpening" @click="openQrLogin()">
          {{ qrOpening ? '正在启动扫码…' : '扫码登录' }}
        </button>
        <button class="btn btn--default" type="button" @click="openCookieImport">Cookie 导入</button>
      </div>
    </div>

    <!-- 列表区：固定高度内部滚动（规范 29），加载遮罩 + 转圈（规范 23） -->
    <div class="shop-page__table-wrap">
      <Loading :visible="loading" text="加载中..." />
      <TableContainer>
        <table class="data-table">
          <thead>
            <tr>
              <th>店铺标识</th>
              <th>店铺名称</th>
              <th>所属成员</th><th>消息通道</th><th>AI 客服</th><th>今日记录回复</th><th>备注</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>更新时间</th>
              <th class="data-table__op">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="shop in shops" :key="shop.id">
              <td><SafeHtml :content="shop.shop_id" /></td>
              <td><SafeHtml :content="shop.shop_name || '-'" /></td>
              <td>{{ shop.owner }}</td><td><span class="tag" :class="shop.connected?'tag--on':'tag--off'">{{ shop.connected?'已连接':'未连接' }}</span><small class="shop-overview-note">{{ shop.sync?.error || (shop.sync?.last_success_at ? '最近同步成功 '+formatDateTime(shop.sync.last_success_at) : '尚无平台同步验证') }}</small></td><td><span class="tag" :class="shop.ai_ready?'tag--on':'tag--off'">{{ shop.ai_ready?'已就绪':shop.ai_enabled?'待配置':'未启用' }}</span><small class="shop-overview-note">{{ shop.ai_ready ? shop.model_name : '' }}</small></td><td>{{ shop.today_replies }}</td><td><SafeHtml :content="shop.remark || '-'" /></td>
              <td>
                <span class="tag" :class="shop.status === 1 ? 'tag--on' : 'tag--off'">
                  {{ statusText(shop.status) }}
                </span>
              </td>
              <td>{{ formatDateTime(shop.created_at) }}</td>
              <td>{{ formatDateTime(shop.updated_at) }}</td>
              <td class="data-table__op">
                <button class="link-btn" type="button" @click="openSettings(shop)">设置</button>
                <button class="link-btn" type="button" @click="openEdit(shop)">编辑</button>
                <button
                  class="link-btn"
                  type="button"
                  title="为这家店铺重新扫码授权；扫码确认到别的店铺不会覆盖当前凭据"
                  @click="openQrLogin(shop)"
                >
                  扫码重授权
                </button>
                <button
                  v-if="shop.status === 1"
                  class="link-btn link-btn--danger"
                  type="button"
                  @click="openDisable(shop)"
                >
                  停用
                </button>
                <button v-else class="link-btn" type="button" @click="enableShop(shop)">启用</button>
              </td>
            </tr>
            <tr v-if="!loading && shops.length === 0">
              <td class="data-table__empty" colspan="11">暂无店铺数据</td>
            </tr>
          </tbody>
        </table>
      </TableContainer>
    </div>

    <!-- 后端分页（规范 28） -->
    <Pagination
      :total="total"
      v-model:page="page"
      v-model:page-size="pageSize"
      @change="loadShops"
    />

    <!-- 新增 / 编辑弹窗（仅关闭按钮关闭，规范 7） -->
    <FormModal
      v-model="formVisible"
      :title="formTitle"
      :loading="saving"
      confirm-text="保存"
      @confirm="submitForm"
    >
      <div class="form">
        <!-- 账号密码登录 / Cookie 导入：店铺标识与名称由登录后自动获取，无需手填 -->
        <p v-if="formMode !== 'edit'" class="form__hint">
          登录成功后将自动获取店铺标识、名称与 Logo，无需手动填写。
        </p>

        <div v-if="formMode === 'edit'" class="form__item">
          <label class="form__label">店铺名称</label>
          <input v-model="form.shop_name" class="form__input" type="text" placeholder="选填，店铺展示名称" />
        </div>

        <!-- 编辑模式：反显并可修改账号 / 密码 / Cookie（隐藏查看，需求 3.6） -->
        <template v-if="formMode === 'edit'">
          <div class="form__item">
            <label class="form__label">登录账号</label>
            <input v-model="form.username" class="form__input" type="text" placeholder="拼多多登录账号" />
          </div>
          <div class="form__item">
            <label class="form__label">登录密码</label>
            <div class="form__password">
              <input
                v-model="form.password"
                class="form__input"
                :type="showPassword ? 'text' : 'password'"
                placeholder="拼多多登录密码"
              />
              <button
                type="button"
                class="form__password-toggle"
                :title="showPassword ? '隐藏' : '显示'"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                :aria-pressed="showPassword"
                @click="showPassword = !showPassword"
              >
                {{ showPassword ? '隐藏' : '显示' }}
              </button>
            </div>
          </div>
          <div class="form__item">
            <label class="form__label">Cookie</label>
            <textarea
              v-model="form.cookies"
              class="form__textarea"
              rows="5"
              placeholder="登录 Cookie 文本（可查看 / 修改）"
            ></textarea>
          </div>
          <p class="form__hint">修改账号 / 密码 / Cookie 后保存即生效；店铺在线时将以新 Cookie 自动重连。</p>
        </template>

        <!-- 账号密码登录字段 -->
        <template v-if="formMode === 'password'">
          <div class="form__item">
            <label class="form__label">登录账号<span class="form__required">*</span></label>
            <input v-model="form.username" class="form__input" type="text" placeholder="拼多多商家后台账号" />
          </div>
          <div class="form__item">
            <label class="form__label">登录密码<span class="form__required">*</span></label>
            <div class="form__password">
              <input
                v-model="form.password"
                class="form__input"
                :type="showPassword ? 'text' : 'password'"
                placeholder="拼多多商家后台密码"
                autocomplete="off"
              />
              <button
                type="button"
                class="form__password-toggle"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                :aria-pressed="showPassword"
                @click="showPassword = !showPassword"
              >
                {{ showPassword ? '隐藏' : '显示' }}
              </button>
            </div>
          </div>
          <p class="form__hint">提交后会显示登录进度和操作窗口。若平台要求验证，请在窗口中由本人完成；核验店铺身份后才会保存。</p>
          <p v-if="saving" class="form__hint" role="status">正在创建登录会话，请勿重复提交。</p>
        </template>

        <!-- Cookie 导入字段 -->
        <template v-if="formMode === 'cookie'">
          <div class="form__item">
            <label class="form__label">Cookie 文本<span class="form__required">*</span></label>
            <textarea
              v-model="form.cookies"
              class="form__textarea"
              rows="5"
              placeholder="请粘贴从拼多多商家后台导出的完整 Cookie 文本"
            ></textarea>
          </div>
          <p class="form__hint">系统将校验 Cookie 有效性，校验通过后保存店铺与凭据。</p>
        </template>

        <div class="form__item">
          <label class="form__label">备注</label>
          <input v-model="form.remark" class="form__input" type="text" placeholder="选填，便于识别的备注" />
        </div>
      </div>
    </FormModal>

    <!--
      扫码登录弹窗（本轮新增）
      只展示服务器取回的二维码图片与状态文案；前端全程接触不到 Cookie。
      「绑定成功」只在身份核验通过且落库成功后才出现（见 bindQrSession）。
    -->
    <FormModal
      v-model="qrVisible"
      :title="qrMode === 'password' ? '账号密码登录' : '扫码登录'"
      :max-width="qrVerifyOpen ? '1200px' : '420px'"
      :loading="qrBinding"
      confirm-text="关闭"
      :cancel-text="qrMode === 'password' ? '取消登录' : '取消扫码'"
      @confirm="closeQrLogin"
      @cancel="closeQrLogin"
    >
      <div class="form qr-login">
        <p v-if="qrMode === 'password'" class="form__hint">
          正在使用您提交的账号登录。如需短信验证，请直接在下方填写；通过后自动核验店铺并保存。
        </p>
        <p v-else-if="qrTargetShopId" class="form__hint">
          正在为店铺 <SafeHtml :content="qrTargetShopId" /> 重新授权。
          若扫码确认的是其他店铺，系统会拒绝保存，当前凭据保持不变。
        </p>
        <p v-else class="form__hint">
          请用<b>拼多多商家版 App</b> 扫描下方二维码并在手机上确认登录。
          确认成功后系统会自动核验店铺身份并保存登录态。
        </p>

        <!-- 正常路径：只展示服务器取回的清晰二维码本身，绝不自动回退成完整浏览器页面。 -->
        <div v-if="!qrVerifyOpen && qrMode !== 'password'" class="qr-login__code">
          <img
            v-if="qrImage"
            :src="qrImage"
            alt="拼多多商家版 App 登录二维码"
            class="qr-login__img"
          />
          <div v-else class="qr-login__placeholder">
            {{ qrError || (qrState === 'verification_required' ? '需要先完成人工安全验证' : '正在获取二维码…') }}
          </div>
        </div>

        <p class="qr-login__state" :class="{ 'qr-login__state--error': !!qrError }">
          {{ qrStatusText() }}
        </p>

        <!--
          仅当平台确实要求人工验证时出现：先如实说明真实状态，再由用户显式决定是否
          打开验证画面。系统不代过验证码；这是可恢复的一次性步骤，通过后取码不再
          重复要求验证，用户随后回到上方二维码主视图继续扫码。
        -->
        <SmsVerificationForm v-if="qrMode === 'password' && qrVerification?.type === 'sms' && !qrTerminal"
          :key="qrSessionId" :session-id="qrSessionId" :verification="qrVerification" />

        <div v-if="(qrMode === 'password' || qrState === 'verification_required') && !qrTerminal" class="qr-login__verify">
          <p v-if="qrMode !== 'password'" class="qr-login__verify-tip">
            这是平台的安全验证，需要您本人在浏览器画面里完成（关闭提示弹窗、拖动滑块等）。
            验证通过后会返回二维码；平台仍可能再次要求验证。
          </p>
          <button
            v-if="!qrVerifyOpen"
            class="btn btn--primary"
            type="button"
            @click="qrVerifyOpen = true"
          >
            {{ qrMode === 'password' ? (qrVerification?.type === 'captcha' ? '打开图形验证窗口' : '查看登录窗口（备用）') : '开始人工验证' }}
          </button>
          <QrVerificationWindow
            v-else
            :key="qrSessionId"
            :session-id="qrSessionId"
            :login-mode="qrMode"
          />
        </div>
      </div>
    </FormModal>

    <!-- 停用确认弹窗 -->
    <ConfirmModal
      v-model="disableVisible"
      type="danger"
      title="停用店铺"
      :message="`确认停用店铺「${disableTarget && (disableTarget.shop_name || disableTarget.shop_id)}」吗？停用后将断开其拼多多连接，数据将保留。`"
      confirm-text="确认停用"
      :loading="disabling"
      @confirm="confirmDisable"
    />

    <!-- 店铺设置弹窗（默认回复 / AI / 营业时间 / 过滤黑名单 / 风控 / 转人工） -->
    <ShopSettingsModal v-model="settingsVisible" :shop="settingsShop" />
  </div>
</template>

<style scoped>
.shop-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.shop-page__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.shop-page__filters {
  display: flex;
  gap: 12px;
}

.shop-page__filter-item {
  width: 160px;
}

.shop-page__actions {
  display: flex;
  gap: 10px;
}

/* 表格外层：相对定位以承载区域加载遮罩；固定高度内部滚动 */
.shop-page__table-wrap {
  position: relative;
  flex: 1;
  min-height: 240px;
}

/* 通用按钮样式 */
.btn {
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  border: 1px solid var(--color-border, #e5e6eb);
  transition: background 0.15s ease, border-color 0.15s ease;
}
.btn--primary {
  background: var(--color-primary, #1677ff);
  color: var(--color-on-primary, #ffffff);
  border-color: var(--color-primary, #1677ff);
}
.btn--primary:hover {
  background: var(--color-primary-hover, #4096ff);
}
.btn--default {
  background: var(--color-bg-elevated, #ffffff);
  color: var(--color-text, #1f2329);
}
.btn--default:hover {
  border-color: var(--color-primary, #1677ff);
  color: var(--color-primary, #1677ff);
}

/* 数据表格 */
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
.data-table__op {
  width: 220px;
}
.data-table__empty {
  text-align: center;
  color: var(--color-text-secondary, #5c6573);
  padding: 32px 0;
}

/* 状态标签 */
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

/* 行内操作按钮 */
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

/* 表单 */
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
.form__password {
  position: relative;
  display: flex;
}
.form__password .form__input {
  padding-right: 56px;
}
.form__password-toggle {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  padding: 2px 8px;
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 13px;
  color: var(--color-primary, #1677ff);
}
.form__hint {
  font-size: 12px;
  color: var(--color-text-secondary, #5c6573);
  line-height: 1.5;
}

/* 扫码登录弹窗 */
.qr-login {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.qr-login__code {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 200px;
  padding: 12px;
  border: 1px solid var(--color-border, #e3e6eb);
  border-radius: 8px;
  background: #fff;
}
.qr-login__img {
  width: 240px;
  height: 240px;
  padding: 16px;
  box-sizing: content-box;
  background: #fff;
  opacity: 1;
  image-rendering: pixelated;
}
.qr-login__placeholder {
  font-size: 13px;
  color: var(--color-text-secondary, #5c6573);
  text-align: center;
  line-height: 1.6;
  padding: 0 12px;
}
.qr-login__state {
  font-size: 14px;
  text-align: center;
  color: var(--color-text, #1f2329);
}
.qr-login__state--error {
  color: var(--color-danger, #d93026);
}
.qr-login__notice {
  font-size: 12px;
  line-height: 1.6;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--color-warning-bg, #fff7e6);
  color: var(--color-text, #1f2329);
}
.qr-login__actions {
  display: flex;
  justify-content: center;
}
.qr-login__verify {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.qr-login__verify-tip {
  font-size: 12px;
  line-height: 1.6;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--color-warning-bg, #fff7e6);
  color: var(--color-text, #1f2329);
}
</style>

<style scoped>.shop-overview-search{display:flex;gap:8px}.shop-overview-search input{border:1px solid #dce4ef;border-radius:6px;padding:9px;font:inherit;font-size:12px;min-width:170px}.shop-overview-note{display:block;color:#8a96a8;font-size:10px;max-width:190px;white-space:normal;margin-top:6px;line-height:1.5}.shop-page__toolbar,.shop-page__filters{flex-wrap:wrap;gap:10px}.data-table{min-width:1350px}</style>

<style scoped>
.shop-platform-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}
.shop-platform-heading h1{margin:0 0 5px;font-size:20px;font-weight:600}.shop-platform-heading p{margin:0;font-size:12px;color:#8290a4}
.shop-platform-heading a,.platform-unsupported a{font-size:13px;color:#2866ee}
.platform-unsupported{padding:24px;line-height:1.8}.platform-unsupported h1{font-size:20px}
</style>
