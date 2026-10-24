<!-- 顶部标签页（tabs）组件：拼多多自动回复系统前端 -->
<!-- 风格与参考项目 xianyu-auto-reply-wangpan 一致：tab-item 圆角胶囊、首页带 Home 图标、关闭按钮。 -->
<!-- 功能： -->
<!--   1. 每打开一个菜单即在顶部生成一个标签（规范 31），支持点击切换与关闭； -->
<!--   2. 在标签上点击右键弹出操作菜单（参照参考项目顶部导航右键操作）： -->
<!--      关闭当前 / 关闭右侧 / 关闭左侧 / 关闭所有；首页（仪表盘）不可被「关闭当前」。 -->
<!-- 标签状态由 store/tabs_store 维护；标题取自路由 meta.title。 -->
<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Home, X } from 'lucide-vue-next'
import { useTabsStore } from '@/store'
import { TABS_HOME_PATH } from '@/store/tabs_store'

const router = useRouter()
const route = useRoute()
const tabsStore = useTabsStore()

// 右键菜单状态：是否可见、显示坐标、右键所针对的标签路径
const contextMenu = ref({ visible: false, x: 0, y: 0, targetPath: '' })
// 右键菜单 DOM 引用，用于「点击菜单外区域关闭」判断
const menuRef = ref(null)

// 根据当前路由同步标签（仅对带 meta.title 的路由建立标签）
function syncTabFromRoute() {
  const title = route.meta && route.meta.title
  if (title) {
    tabsStore.addTab({ path: route.path, title })
  } else {
    tabsStore.setActiveTab(route.path)
  }
}

onMounted(syncTabFromRoute)
watch(() => route.path, syncTabFromRoute)

function onTabClick(path) {
  if (path !== route.path) {
    router.push(path)
  } else {
    tabsStore.setActiveTab(path)
  }
}

function onTabClose(path) {
  const next = tabsStore.removeTab(path)
  if (next && next !== route.path) {
    router.push(next)
  }
}

// —— 右键菜单 ——
// 在标签上触发右键：阻止浏览器默认菜单，记录目标标签并在鼠标位置展示自定义菜单
function onTabContextMenu(e, path) {
  e.preventDefault()
  contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, targetPath: path }
}

// 关闭右键菜单
function closeContextMenu() {
  contextMenu.value.visible = false
}

// 当前右键目标标签在标签列表中的索引（用于判断左右是否存在可关闭标签）
const targetIndex = computed(() =>
  tabsStore.tabs.findIndex((t) => t.path === contextMenu.value.targetPath),
)
// 右侧是否存在标签（存在则「关闭右侧」可用）
const hasRightTabs = computed(
  () => targetIndex.value > -1 && targetIndex.value < tabsStore.tabs.length - 1,
)
// 左侧是否存在「首页之外」的可关闭标签（仅首页时「关闭左侧」不可用）
const hasLeftTabs = computed(() => {
  if (targetIndex.value <= 0) {
    return false
  }
  // 目标左侧若仅有首页一个标签，则无可关闭的左侧标签
  return tabsStore.tabs.slice(0, targetIndex.value).some((t) => t.path !== TABS_HOME_PATH)
})
// 目标是否为首页（首页禁止「关闭当前」）
const isHomeTarget = computed(() => contextMenu.value.targetPath === TABS_HOME_PATH)

// 关闭当前标签
function handleCloseCurrent() {
  if (!isHomeTarget.value) {
    onTabClose(contextMenu.value.targetPath)
  }
  closeContextMenu()
}

// 关闭右侧标签
function handleCloseRight() {
  const next = tabsStore.removeTabsToRight(contextMenu.value.targetPath)
  if (next && next !== route.path) {
    router.push(next)
  }
  closeContextMenu()
}

// 关闭左侧标签
function handleCloseLeft() {
  const next = tabsStore.removeTabsToLeft(contextMenu.value.targetPath)
  if (next && next !== route.path) {
    router.push(next)
  }
  closeContextMenu()
}

// 关闭所有标签（保留首页并回到首页）
function handleCloseAll() {
  const next = tabsStore.removeAllTabs()
  if (next && next !== route.path) {
    router.push(next)
  }
  closeContextMenu()
}

// 全局点击：点击菜单以外区域时关闭右键菜单
function onDocumentClick(e) {
  if (!contextMenu.value.visible) {
    return
  }
  if (menuRef.value && menuRef.value.contains(e.target)) {
    return
  }
  closeContextMenu()
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <div class="tabs-bar scrollbar-hide">
    <div class="flex min-w-max gap-1">
      <div
        v-for="tab in tabsStore.tabs"
        :key="tab.path"
        :class="[tabsStore.activeTab === tab.path ? 'tab-item-active' : 'tab-item', 'flex-shrink-0']"
        @click="onTabClick(tab.path)"
        @contextmenu="onTabContextMenu($event, tab.path)"
      >
        <Home v-if="tab.path === '/dashboard'" class="w-3.5 h-3.5" />
        <span class="text-xs sm:text-sm">{{ tab.title }}</span>
        <button
          v-if="tab.closable"
          type="button"
          class="tab-close"
          title="关闭标签"
          @click.stop="onTabClose(tab.path)"
        >
          <X class="w-3 h-3" />
        </button>
      </div>
    </div>

    <!-- 右键操作菜单：固定定位到鼠标位置，点击菜单外区域自动关闭 -->
    <div
      v-if="contextMenu.visible"
      ref="menuRef"
      class="tabs-context-menu"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
    >
      <button
        type="button"
        class="tabs-context-item"
        :disabled="isHomeTarget"
        @click="handleCloseCurrent"
      >
        关闭当前
      </button>
      <button
        type="button"
        class="tabs-context-item"
        :disabled="!hasRightTabs"
        @click="handleCloseRight"
      >
        关闭右侧
      </button>
      <button
        type="button"
        class="tabs-context-item"
        :disabled="!hasLeftTabs"
        @click="handleCloseLeft"
      >
        关闭左侧
      </button>
      <div class="tabs-context-divider" />
      <button
        type="button"
        class="tabs-context-item tabs-context-item-danger"
        @click="handleCloseAll"
      >
        关闭所有
      </button>
    </div>
  </div>
</template>
