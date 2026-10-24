<!-- 应用根组件：承载全局路由出口（router-view） -->
<!-- 整体布局（左侧导航 + 右侧内容 + 顶部面包屑）将在后续布局任务中由 components/layout 实现 -->
<!-- 全局通用组件：Toast（替代 alert，规范 6）与全局加载遮罩（遮罩+转圈，规范 23）常驻挂载 -->
<script setup>
import { Toast, Loading } from '@/components/common'
import { useUIStore } from '@/store/ui_store'

// 全局加载遮罩由 ui_store 的 globalLoading 控制
const uiStore = useUIStore()
</script>

<template>
  <div id="app-root">
    <!-- 路由匹配到的页面在此渲染 -->
    <router-view />

    <!-- 全局消息提示：挂载后自动注入 showToast 正式实现 -->
    <Toast />

    <!-- 全局加载遮罩 + 转圈：由 ui_store.globalLoading 控制 -->
    <Loading :visible="uiStore.globalLoading" full-screen text="加载中..." />
  </div>
</template>

<style scoped>
#app-root {
  height: 100%;
}
</style>
