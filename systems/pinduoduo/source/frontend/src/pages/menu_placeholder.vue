<!-- 菜单占位页面：由集中菜单配置自动生成路由时的统一占位组件 -->
<!-- 各业务页面将在任务 17 中实现并替换对应路由的 component（见 router/index.js） -->
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 读取当前路由 meta.title 作为占位标题（由菜单配置注入，规范 27 全中文）
const route = useRoute()
const title = computed(() => route.meta?.title || '页面')
</script>

<template>
  <div class="menu-placeholder">
    <h2>{{ title }}</h2>
    <p>该页面将在后续业务页面任务中实现。</p>
  </div>
</template>

<style scoped>
.menu-placeholder {
  padding: 24px;
}
.menu-placeholder h2 {
  margin-bottom: 12px;
  color: var(--color-text);
}
.menu-placeholder p {
  color: var(--color-text);
  opacity: 0.7;
}
</style>
