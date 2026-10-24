<!-- 左侧导航栏组件：拼多多自动回复系统前端 -->
<!-- 风格与参考项目 xianyu-auto-reply-wangpan 一致：白/暗色侧栏、lucide 图标、蓝色选中态、 -->
<!-- 分组可展开/折叠、桌面端收起仅显示图标、移动端抽屉 + 遮罩、底部收起切换按钮。 -->
<!-- 功能： -->
<!--   1. 菜单数据源为集中菜单配置 config/navigation.js（菜单与路由同源，规范 30）， -->
<!--      按当前用户权限过滤后渲染（需求 2.5/2.6/2.9）； -->
<!--   2. 桌面端支持收起/展开；移动端抽屉显隐（响应式，规范 20）； -->
<!--   3. 蓝白主色调，暗黑模式经 Tailwind dark 变体自适应（规范 24/25/26）。 -->
<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { Menu, X, PanelLeftClose, PanelLeft, ChevronDown, MessageSquare } from 'lucide-vue-next'
import { useLayoutStore, useUserStore } from '@/store'
import {
  getVisibleMainNavItems,
  getVisibleAdminNavItems,
  getVisibleBottomNavItems,
  isNavGroup,
} from '@/config/navigation'
import AppIcon from './AppIcon.vue'
import { chatAttention, startChatAttention, stopChatAttention } from '@/store/chat_attention'

defineProps({
  // 系统名称（顶部品牌区展示，规范 25）
  systemName: {
    type: String,
    default: 'OIN',
  },
})

const router = useRouter()
const route = useRoute()
const uiStore = useLayoutStore()
const userStore = useUserStore()

const { isAdmin, authorizedResources } = storeToRefs(userStore)

// 菜单过滤选项：管理员标记、被授权资源键（需求 2.5/2.6/2.9）
const filterOptions = computed(() => ({
  isAdmin: isAdmin.value,
  authorizedResources: authorizedResources.value,
}))

const mainItems = computed(() => getVisibleMainNavItems(filterOptions.value))
const adminItems = computed(() => getVisibleAdminNavItems(filterOptions.value))
const bottomItems = computed(() => getVisibleBottomNavItems(filterOptions.value))
let attentionToken = ''
watch(() => [authorizedResources.value, userStore.token], ([resources, token]) => {
  if (token !== attentionToken) { stopChatAttention(); attentionToken = token }
  if (resources === null) return
  if (token && mainItems.value.some(item => item.path === '/online-chat')) startChatAttention()
  else stopChatAttention()
}, { immediate: true })

const collapsed = computed(() => uiStore.sidebarCollapsed)
const mobileOpen = computed(() => uiStore.sidebarMobileOpen)
// 是否显示文字标签（桌面收起态仅显示图标；移动端抽屉打开时始终显示文字）
const showLabel = computed(() => !collapsed.value || mobileOpen.value)

// 已展开的分组键集合
const expandedGroups = ref(new Set())

// 判断菜单项是否处于激活状态（精确匹配或作为前缀匹配子路由）
function isActive(path) {
  if (!path) {
    return false
  }
  return route.path === path || route.path.startsWith(`${path}/`)
}

// 判断分组是否有处于激活态的子项
function isGroupActive(group) {
  return group.children.some((child) => isActive(child.path))
}

// 切换分组展开/折叠
function toggleGroup(key) {
  const next = new Set(expandedGroups.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedGroups.value = next
}

// 路由变化时自动展开命中的分组
watch(
  () => route.path,
  () => {
    const groups = [...mainItems.value, ...adminItems.value].filter(isNavGroup)
    for (const group of groups) {
      if (isGroupActive(group) && !expandedGroups.value.has(group.key)) {
        const next = new Set(expandedGroups.value)
        next.add(group.key)
        expandedGroups.value = next
      }
    }
  },
  { immediate: true }
)

// 监听窗口尺寸：平板自动收起、桌面自动展开（与参考项目一致）
function handleResize() {
  const width = window.innerWidth
  if (width >= 640 && width < 1024) {
    uiStore.setSidebarCollapsed(true)
  } else if (width >= 1024) {
    uiStore.setSidebarCollapsed(false)
  }
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
})
onUnmounted(() => {
  stopChatAttention()
  window.removeEventListener('resize', handleResize)
})

// 点击菜单项：导航并在移动端关闭抽屉
function onMenuClick(path) {
  router.push(path)
  uiStore.setSidebarMobileOpen(false)
}

function closeMobile() {
  uiStore.setSidebarMobileOpen(false)
}

function openMobile() {
  uiStore.setSidebarMobileOpen(true)
}

function toggleCollapsed() {
  uiStore.toggleSidebar()
}
</script>

<template>
  <!-- 移动端遮罩层：抽屉打开时显示，点击关闭 -->
  <div
    class="fixed inset-0 bg-black/60 z-40 sm:hidden transition-opacity duration-200"
    :class="mobileOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'"
    @click="closeMobile"
  />

  <!-- 侧边栏 -->
  <aside
    class="fixed top-0 left-0 h-screen z-50 bg-white dark:bg-slate-900 flex flex-col
           border-r border-slate-200 dark:border-slate-700
           transition-[width,transform] duration-200 ease-out"
    :class="[
      mobileOpen ? 'translate-x-0' : '-translate-x-full sm:translate-x-0',
      mobileOpen ? 'w-72' : collapsed ? 'w-16' : 'w-56',
    ]"
  >
    <!-- 品牌区 -->
    <div
      class="h-14 flex items-center border-b border-slate-200 dark:border-slate-700"
      :class="!showLabel ? 'justify-center px-2' : 'justify-between px-4'"
    >
      <div class="flex items-center gap-2.5 min-w-0">
        <div class="w-8 h-8 rounded-lg bg-primary-500 flex items-center justify-center flex-shrink-0">
          <MessageSquare class="w-4 h-4 text-white" />
        </div>
        <span
          v-if="showLabel"
          class="font-semibold text-sm text-slate-900 dark:text-white truncate max-w-[150px]"
        >{{ systemName }}</span>
      </div>
      <button
        v-if="mobileOpen"
        type="button"
        class="sm:hidden p-1.5 rounded text-slate-400 hover:text-slate-900 dark:hover:text-white
               hover:bg-slate-100 dark:hover:bg-white/10 transition-colors"
        title="关闭导航"
        @click="closeMobile"
      >
        <X class="w-4 h-4" />
      </button>
    </div>

    <!-- 导航菜单 -->
    <nav
      class="flex-1 overflow-y-auto py-3 space-y-0.5 sidebar-scrollbar"
      :class="!showLabel ? 'px-1.5' : 'px-2'"
    >
      <!-- 主导航（含分组） -->
      <template v-for="entry in mainItems" :key="entry.key">
        <!-- 分组菜单 -->
        <div v-if="isNavGroup(entry)">
          <button
            type="button"
            class="flex items-center gap-3 w-full px-3 py-2.5 rounded-md text-sm transition-all duration-150"
            :class="[
              !showLabel ? 'justify-center px-2' : '',
              isGroupActive(entry)
                ? 'text-primary-600 dark:text-primary-400'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10',
            ]"
            :title="!showLabel ? entry.label : undefined"
            @click="toggleGroup(entry.key)"
          >
            <AppIcon :name="entry.icon" :size="16" class="flex-shrink-0" />
            <template v-if="showLabel">
              <span class="truncate flex-1 text-left">{{ entry.label }}</span>
              <ChevronDown
                class="w-4 h-4 transition-transform duration-300"
                :class="{ 'rotate-180': expandedGroups.has(entry.key) }"
              />
            </template>
          </button>
          <!-- 子菜单（展开且显示标签时渲染） -->
          <div v-if="showLabel && expandedGroups.has(entry.key)" class="py-1 space-y-0.5">
            <button
              v-for="child in entry.children"
              :key="child.key"
              type="button"
              class="flex items-center gap-3 w-full pl-9 pr-3 py-2.5 rounded-md text-sm transition-all duration-150"
              :class="isActive(child.path)
                ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10'"
              @click="onMenuClick(child.path)"
            >
              <AppIcon :name="child.icon" :size="16" class="flex-shrink-0" />
              <span class="truncate">{{ child.label }}</span>
            </button>
          </div>
        </div>
        <!-- 叶子菜单 -->
        <button
          v-else
          type="button"
          class="flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-all duration-150 w-full"
          :class="[
            !showLabel ? 'justify-center px-2' : '',
            isActive(entry.path)
              ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10',
          ]"
          :title="!showLabel ? entry.label : undefined"
          @click="onMenuClick(entry.path)"
        >
          <AppIcon :name="entry.icon" :size="16" class="flex-shrink-0" />
          <span v-if="showLabel" class="truncate">{{ entry.label }}</span>
          <span v-if="entry.path==='/online-chat'&&chatAttention.total" class="ml-auto rounded-full bg-red-600 px-1.5 text-xs leading-5 text-white" :aria-label="`${chatAttention.total} 项待人工处理`">{{ chatAttention.total>99?'99+':chatAttention.total }}</span>
        </button>
      </template>

      <!-- 管理端菜单（仅管理员可见，需求 2.5/2.9/21.17） -->
      <template v-if="adminItems.length > 0">
        <div v-if="showLabel" class="pt-4 pb-2 px-3">
          <p class="text-xs font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">管理端</p>
        </div>
        <div v-else class="pt-2 border-t border-slate-200 dark:border-slate-700 mt-2" />
        <button
          v-for="entry in adminItems"
          :key="entry.key"
          type="button"
          class="flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-all duration-150 w-full"
          :class="[
            !showLabel ? 'justify-center px-2' : '',
            isActive(entry.path)
              ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10',
          ]"
          :title="!showLabel ? entry.label : undefined"
          @click="onMenuClick(entry.path)"
        >
          <AppIcon :name="entry.icon" :size="16" class="flex-shrink-0" />
          <span v-if="showLabel" class="truncate">{{ entry.label }}</span>
        </button>
      </template>

      <!-- 底部菜单（教程 / 反馈 / 免责声明 / 关于） -->
      <template v-if="bottomItems.length > 0">
        <div v-if="showLabel" class="pt-4 pb-2 px-3">
          <p class="text-xs font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider">其他</p>
        </div>
        <div v-else class="pt-2 border-t border-slate-200 dark:border-slate-700 mt-2" />
        <button
          v-for="entry in bottomItems"
          :key="entry.key"
          type="button"
          class="flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-all duration-150 w-full"
          :class="[
            !showLabel ? 'justify-center px-2' : '',
            isActive(entry.path)
              ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-sm'
              : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10',
          ]"
          :title="!showLabel ? entry.label : undefined"
          @click="onMenuClick(entry.path)"
        >
          <AppIcon :name="entry.icon" :size="16" class="flex-shrink-0" />
          <span v-if="showLabel" class="truncate">{{ entry.label }}</span>
        </button>
      </template>
    </nav>

    <!-- 桌面端收起/展开切换 -->
    <div class="hidden lg:flex items-center justify-center p-2 border-t border-slate-200 dark:border-slate-700">
      <button
        type="button"
        class="p-2 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-white
               hover:bg-slate-100 dark:hover:bg-white/10 transition-colors"
        :title="collapsed ? '展开侧边栏' : '收起侧边栏'"
        @click="toggleCollapsed"
      >
        <PanelLeft v-if="collapsed" class="w-4 h-4" />
        <PanelLeftClose v-else class="w-4 h-4" />
      </button>
    </div>
  </aside>

  <!-- 移动端打开按钮 -->
  <button
    type="button"
    class="fixed top-2.5 left-2.5 z-50 sm:hidden w-8 h-8 rounded-md bg-primary-500 text-white shadow-md
           flex items-center justify-center hover:bg-primary-600 active:scale-95 transition-all"
    :class="{ 'pointer-events-none opacity-0': mobileOpen }"
    title="打开导航"
    :aria-label="chatAttention.total ? `打开导航，${chatAttention.total} 项待人工处理` : '打开导航'"
    @click="openMobile"
  >
    <Menu class="w-4 h-4" />
    <span v-if="chatAttention.total" class="absolute -top-1 -right-1 min-w-4 rounded-full bg-red-600 px-1 text-[10px] leading-4" :aria-label="`${chatAttention.total} 项待人工处理`">{{ chatAttention.total>99?'99+':chatAttention.total }}</span>
  </button>
</template>
