<!-- 整体布局组件：拼多多自动回复系统前端 -->
<!-- 结构：左侧导航栏（Sidebar） + 右侧内容区（顶部栏 TopNavbar + 标签页 TabsBar + 路由视图）。 -->
<!-- 风格：与参考项目 xianyu-auto-reply-wangpan 一致，基于 Tailwind 的蓝白/slate 设计系统， -->
<!--       支持暗黑模式、主题色、导航显隐、响应式适配（规范 20/24/25/26/31）。 -->
<script setup>
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useLayoutStore, useUserStore } from '@/store'
import Sidebar from './Sidebar.vue'
import TopNavbar from './TopNavbar.vue'
import TabsBar from './TabsBar.vue'

defineProps({
  // 系统名称（透传给侧边栏品牌区与顶栏欢迎语）
  systemName: {
    type: String,
    default: 'OIN',
  },
})

const uiStore = useLayoutStore()
const userStore = useUserStore()
const route = useRoute()

// 应用挂载时确保主题已初始化（与 main.js 初始化互为兜底，幂等）
onMounted(async () => {
  uiStore.initTheme()
  // 刷新页面后恢复登录态时，重新拉取菜单授权资源以按权限渲染菜单（需求 2.6）。
  // 仅在已登录且尚未加载时拉取；失败不阻断界面（保持 null=不限制，后端仍判权）。
  if (userStore.isLoggedIn && userStore.authorizedResources === null) {
    try {
      await userStore.loadAuthorizedResources()
    } catch (e) {
      // 静默：授权资源拉取失败时菜单不做前端限制，后端接口仍保护实际访问
    }
  }
})
</script>

<template>
  <div class="h-screen bg-slate-50 dark:bg-slate-900 transition-colors duration-200">
    <!-- 左侧导航栏 -->
    <Sidebar :system-name="systemName" />

    <!-- 右侧内容区：占满视口高度，响应侧边栏收起状态（移动端无边距；桌面端 16/56） -->
    <div
      class="h-screen flex flex-col transition-[margin] duration-200 ml-0 sm:ml-16"
      :class="{ 'sm:ml-56': !uiStore.sidebarCollapsed }"
    >
      <!-- 固定顶部区：顶部栏 + 标签页（不随内容滚动） -->
      <div class="flex-shrink-0 z-40 bg-slate-50 dark:bg-slate-900">
        <TopNavbar :system-name="systemName" />
        <TabsBar v-if="route.path !== '/online-chat'" />
      </div>

      <!-- 页面内容（路由视图）：内部滚动，避免浏览器窗口整体滚动（规范 29） -->
      <main class="flex-1 min-h-0" :class="route.path === '/online-chat' ? 'flex flex-col overflow-hidden p-2' : 'overflow-auto p-3 sm:p-4 lg:p-6'">
        <router-view />
      </main>
    </div>
  </div>
</template>
