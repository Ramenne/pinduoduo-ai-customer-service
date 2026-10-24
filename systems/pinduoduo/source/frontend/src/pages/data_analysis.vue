<!--
  数据分析页面（需求 20.2 / 20.3）
  职责：按北京时间口径展示指定时间范围内「按天聚合」的消息量与回复量趋势。
        数据经 backend /dashboard/trend 获取（后端统计，前端仅展示）。
  实现：以内联 SVG 绘制双折线图（工程未引入图表库），并提供日趋势数据表格便于核对。
  规范：加载遮罩 + 转圈（规范 23）、全中文（规范 27）、响应式（规范 20）、
        表格固定高度内部滚动（规范 29）、错误提示统一 showToast（规范 2/4）、不写死 localhost（规范 21）。
-->
<script setup>
import { computed, onMounted, ref } from 'vue'
import { Loading, TableContainer } from '@/components/common'
import { fetchDashboardTrend } from '@/api/dashboard_api'
import { formatNumber } from '@/utils/format'

// 加载态
const loading = ref(false)

// 起止日期筛选（YYYY-MM-DD，北京时间口径）；为空时由后端默认最近 7 天
const startDate = ref('')
const endDate = ref('')

// 趋势数据点：[{ date, messages, replies }]
const points = ref([])

// 拉取趋势数据（失败提示由请求封装统一处理）
async function loadTrend() {
  loading.value = true
  try {
    const params = {}
    if (startDate.value) {
      params.start_date = startDate.value
    }
    if (endDate.value) {
      params.end_date = endDate.value
    }
    const data = await fetchDashboardTrend(params)
    points.value = (data && data.points) || []
  } finally {
    loading.value = false
  }
}

// 重置筛选并重新查询
function onReset() {
  startDate.value = ''
  endDate.value = ''
  loadTrend()
}

onMounted(loadTrend)

// ---------------------------------------------------------------------------
// 折线图几何计算（内联 SVG，不依赖图表库）
// ---------------------------------------------------------------------------
// 图表视图盒尺寸（viewBox 坐标系，随容器自适应缩放）
const VIEW_WIDTH = 760
const VIEW_HEIGHT = 280
// 内边距（留出坐标轴与标签空间）
const PADDING = { top: 20, right: 20, bottom: 36, left: 48 }

// 绘图区宽高
const plotWidth = VIEW_WIDTH - PADDING.left - PADDING.right
const plotHeight = VIEW_HEIGHT - PADDING.top - PADDING.bottom

// Y 轴最大值（消息量与回复量的最大值，至少为 1，避免除零）
const maxValue = computed(() => {
  let max = 0
  for (const p of points.value) {
    max = Math.max(max, Number(p.messages) || 0, Number(p.replies) || 0)
  }
  return Math.max(max, 1)
})

// 计算某数据点在某序列下的 X/Y 坐标
function pointX(index) {
  const count = points.value.length
  if (count <= 1) {
    return PADDING.left + plotWidth / 2
  }
  return PADDING.left + (plotWidth * index) / (count - 1)
}

function pointY(value) {
  const ratio = (Number(value) || 0) / maxValue.value
  return PADDING.top + plotHeight - ratio * plotHeight
}

// 生成某序列（messages / replies）的折线 polyline points 字符串
function buildPolyline(field) {
  return points.value
    .map((p, idx) => `${pointX(idx)},${pointY(p[field])}`)
    .join(' ')
}

const messageLine = computed(() => buildPolyline('messages'))
const replyLine = computed(() => buildPolyline('replies'))

// Y 轴刻度（0、1/2、最大值三档）
const yTicks = computed(() => {
  const max = maxValue.value
  return [
    { value: max, y: pointY(max) },
    { value: Math.round(max / 2), y: pointY(max / 2) },
    { value: 0, y: pointY(0) },
  ]
})

// X 轴标签：点较多时稀疏显示，避免重叠（最多约 8 个标签）
const xLabels = computed(() => {
  const count = points.value.length
  if (count === 0) {
    return []
  }
  const step = Math.max(1, Math.ceil(count / 8))
  const labels = []
  points.value.forEach((p, idx) => {
    if (idx % step === 0 || idx === count - 1) {
      // 仅展示 MM-DD，节省横向空间
      labels.push({ x: pointX(idx), text: String(p.date).slice(5) })
    }
  })
  return labels
})

// 是否有数据（控制空状态展示）
const hasData = computed(() => points.value.length > 0)
</script>

<template>
  <div class="analysis">
    <h2 class="analysis__title">数据分析</h2>

    <!-- 筛选区：起止日期 + 查询 / 重置 -->
    <div class="analysis__filters">
      <label class="filter-item">
        <span class="filter-item__label">起始日期</span>
        <input v-model="startDate" type="date" class="filter-item__input" />
      </label>
      <label class="filter-item">
        <span class="filter-item__label">结束日期</span>
        <input v-model="endDate" type="date" class="filter-item__input" />
      </label>
      <div class="analysis__filter-actions">
        <button type="button" class="btn btn--primary" :disabled="loading" @click="loadTrend">查询</button>
        <button type="button" class="btn" :disabled="loading" @click="onReset">重置</button>
      </div>
    </div>

    <!-- 图表与数据表（相对定位承载加载遮罩） -->
    <div class="analysis__body">
      <!-- 折线图卡片 -->
      <div class="chart-card">
        <div class="chart-card__legend">
          <span class="legend-item"><i class="legend-dot legend-dot--msg"></i>消息量</span>
          <span class="legend-item"><i class="legend-dot legend-dot--reply"></i>回复量</span>
        </div>

        <svg
          v-if="hasData"
          class="chart-svg"
          :viewBox="`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`"
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label="消息量与回复量趋势折线图"
        >
          <!-- Y 轴刻度线与标签 -->
          <g class="chart-axis">
            <line
              v-for="tick in yTicks"
              :key="`grid-${tick.value}`"
              :x1="PADDING.left"
              :y1="tick.y"
              :x2="VIEW_WIDTH - PADDING.right"
              :y2="tick.y"
              class="chart-grid-line"
            />
            <text
              v-for="tick in yTicks"
              :key="`ylabel-${tick.value}`"
              :x="PADDING.left - 8"
              :y="tick.y + 4"
              text-anchor="end"
              class="chart-axis-label"
            >{{ tick.value }}</text>
          </g>

          <!-- X 轴标签 -->
          <g class="chart-axis">
            <text
              v-for="label in xLabels"
              :key="`xlabel-${label.x}`"
              :x="label.x"
              :y="VIEW_HEIGHT - PADDING.bottom + 20"
              text-anchor="middle"
              class="chart-axis-label"
            >{{ label.text }}</text>
          </g>

          <!-- 消息量折线 -->
          <polyline :points="messageLine" class="chart-line chart-line--msg" />
          <!-- 回复量折线 -->
          <polyline :points="replyLine" class="chart-line chart-line--reply" />
        </svg>

        <!-- 空状态 -->
        <div v-else class="chart-empty">所选范围内暂无数据</div>
      </div>

      <!-- 日趋势数据表（固定高度内部滚动，规范 29） -->
      <div class="analysis__table-wrap">
        <TableContainer max-height="320px">
          <table class="data-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>消息量</th>
                <th>回复量</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in points" :key="p.date">
                <td>{{ p.date }}</td>
                <td>{{ formatNumber(p.messages) }}</td>
                <td>{{ formatNumber(p.replies) }}</td>
              </tr>
              <tr v-if="!hasData">
                <td colspan="3" class="data-table__empty">暂无数据</td>
              </tr>
            </tbody>
          </table>
        </TableContainer>
      </div>

      <Loading :visible="loading" text="加载中..." />
    </div>
  </div>
</template>

<style scoped>
.analysis {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

.analysis__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}

/* 筛选区 */
.analysis__filters {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-item__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.filter-item__input {
  padding: 8px 12px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
}

.analysis__filter-actions {
  display: flex;
  gap: 10px;
}

.btn {
  padding: 8px 18px;
  font-size: 14px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg-elevated);
  color: var(--color-text);
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.btn--primary {
  background: var(--color-primary);
  color: var(--color-on-primary);
  border-color: var(--color-primary);
}

.btn--primary:hover:not(:disabled) {
  background: var(--color-primary-hover);
  color: var(--color-on-primary);
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* 图表与表格区 */
.analysis__body {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chart-card {
  padding: 16px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: var(--shadow-card);
}

.chart-card__legend {
  display: flex;
  gap: 20px;
  margin-bottom: 8px;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.legend-dot {
  width: 12px;
  height: 4px;
  border-radius: 2px;
  display: inline-block;
}

.legend-dot--msg {
  background: #1677ff;
}

.legend-dot--reply {
  background: #15a97c;
}

.chart-svg {
  width: 100%;
  height: auto;
}

.chart-grid-line {
  stroke: var(--color-border);
  stroke-width: 1;
}

.chart-axis-label {
  fill: var(--color-text-secondary);
  font-size: 12px;
}

.chart-line {
  fill: none;
  stroke-width: 2;
}

.chart-line--msg {
  stroke: #1677ff;
}

.chart-line--reply {
  stroke: #15a97c;
}

.chart-empty {
  padding: 60px 0;
  text-align: center;
  color: var(--color-text-secondary);
  font-size: 14px;
}

/* 数据表 */
.analysis__table-wrap {
  flex: 1;
  min-height: 0;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.data-table th,
.data-table td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text);
  white-space: nowrap;
}

.data-table thead th {
  background: var(--color-bg-elevated);
  color: var(--color-text-secondary);
  font-weight: 600;
}

.data-table__empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 32px 0;
}
</style>
