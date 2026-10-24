<!-- 顶部导航栏组件（规范 25/31） -->
<!-- 职责：随当前路由展示面包屑导航，与左侧菜单同步（规范 31：新增菜单时顶部导航同步）。 -->
<!-- 面包屑数据由集中菜单配置（config/navigation.js）按当前路径派生，保证与侧边栏菜单一致。 -->
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { getBreadcrumbByPath } from '@/config/navigation'

const route = useRoute()

// 当前路径对应的面包屑链（[一级菜单, 叶子菜单]）
const breadcrumbs = computed(() => getBreadcrumbByPath(route.path))
</script>

<template>
  <div class="app-top-navbar">
    <!-- 面包屑导航（规范 31） -->
    <nav class="breadcrumb" aria-label="面包屑">
      <template v-for="(crumb, index) in breadcrumbs" :key="crumb.key">
        <span class="breadcrumb-item" :class="{ current: index === breadcrumbs.length - 1 }">
          {{ crumb.label }}
        </span>
        <span v-if="index < breadcrumbs.length - 1" class="breadcrumb-sep">/</span>
      </template>
    </nav>
  </div>
</template>

<style scoped>
.app-top-navbar {
  display: flex;
  align-items: center;
  height: 48px;
  padding: 0 16px;
  background-color: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
}
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--color-text);
}
.breadcrumb-item {
  opacity: 0.7;
}
.breadcrumb-item.current {
  opacity: 1;
  font-weight: 600;
  color: var(--color-primary);
}
.breadcrumb-sep {
  opacity: 0.4;
}
</style>
