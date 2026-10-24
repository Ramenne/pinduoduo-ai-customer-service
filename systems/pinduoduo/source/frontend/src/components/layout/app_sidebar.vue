<!-- 左侧导航栏组件（规范 25、需求 2.5/2.6/2.9） -->
<!-- 职责：
       1. 从集中菜单配置（config/navigation.js）取菜单数据源，按当前用户权限过滤后渲染；
       2. 普通用户仅渲染被授权的菜单（需求 2.6）；
       3. 管理员专属菜单对管理员强制可见（需求 2.5/2.9）；
       4. 支持分组菜单（父子层级）展开/折叠，点击叶子菜单跳转路由。
     说明：本组件聚焦「菜单数据源接入与按权限渲染」（任务 16.5），整体布局样式与显隐开关
          由布局任务（16.2）进一步完善。 -->
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import {
  getVisibleMainNavItems,
  getVisibleAdminNavItems,
  getVisibleBottomNavItems,
  isNavGroup,
} from '@/config/navigation'
import { useUserStore } from '@/store/user_store'

const route = useRoute()
const userStore = useUserStore()

const { isAdmin, authorizedResources } = storeToRefs(userStore)

// 过滤选项：管理员标记、被授权资源键
const filterOptions = computed(() => ({
  isAdmin: isAdmin.value,
  authorizedResources: authorizedResources.value,
}))

// 可见的主导航 / 管理端 / 底部菜单（保留分组层级）
const mainItems = computed(() => getVisibleMainNavItems(filterOptions.value))
const adminItems = computed(() => getVisibleAdminNavItems(filterOptions.value))
const bottomItems = computed(() => getVisibleBottomNavItems(filterOptions.value))

// 判断某菜单键是否处于激活状态（与当前路由匹配）
function isActive(path) {
  if (!path) {
    return false
  }
  return route.path === path || route.path.startsWith(`${path}/`)
}
</script>

<template>
  <nav class="app-sidebar" aria-label="主导航">
    <!-- 主导航菜单 -->
    <ul class="nav-section">
      <li v-for="entry in mainItems" :key="entry.key" class="nav-item">
        <!-- 分组菜单：展示分组标题与子菜单 -->
        <template v-if="isNavGroup(entry)">
          <div class="nav-group-title">
            <span class="nav-icon">{{ entry.icon }}</span>
            <span class="nav-label">{{ entry.label }}</span>
          </div>
          <ul class="nav-children">
            <li v-for="child in entry.children" :key="child.key">
              <router-link
                :to="child.path"
                class="nav-link nav-child-link"
                :class="{ active: isActive(child.path) }"
              >
                <span class="nav-icon">{{ child.icon }}</span>
                <span class="nav-label">{{ child.label }}</span>
              </router-link>
            </li>
          </ul>
        </template>
        <!-- 叶子菜单：直接跳转 -->
        <router-link
          v-else
          :to="entry.path"
          class="nav-link"
          :class="{ active: isActive(entry.path) }"
        >
          <span class="nav-icon">{{ entry.icon }}</span>
          <span class="nav-label">{{ entry.label }}</span>
        </router-link>
      </li>
    </ul>

    <!-- 管理端菜单（仅管理员可见） -->
    <ul v-if="adminItems.length > 0" class="nav-section nav-section-admin">
      <li class="nav-section-heading">管理端</li>
      <li v-for="entry in adminItems" :key="entry.key" class="nav-item">
        <router-link
          :to="entry.path"
          class="nav-link"
          :class="{ active: isActive(entry.path) }"
        >
          <span class="nav-icon">{{ entry.icon }}</span>
          <span class="nav-label">{{ entry.label }}</span>
        </router-link>
      </li>
    </ul>

    <!-- 底部菜单 -->
    <ul v-if="bottomItems.length > 0" class="nav-section nav-section-bottom">
      <li v-for="entry in bottomItems" :key="entry.key" class="nav-item">
        <router-link
          :to="entry.path"
          class="nav-link"
          :class="{ active: isActive(entry.path) }"
        >
          <span class="nav-icon">{{ entry.icon }}</span>
          <span class="nav-label">{{ entry.label }}</span>
        </router-link>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.app-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
  padding: 8px 0;
  background-color: var(--color-bg);
  border-right: 1px solid var(--color-border);
}
.nav-section {
  list-style: none;
}
.nav-section-admin,
.nav-section-bottom {
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border);
}
.nav-section-heading {
  padding: 6px 16px;
  font-size: 12px;
  color: var(--color-text);
  opacity: 0.5;
}
.nav-group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  font-size: 13px;
  color: var(--color-text);
  opacity: 0.7;
}
.nav-children {
  list-style: none;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  color: var(--color-text);
  text-decoration: none;
  font-size: 14px;
}
.nav-child-link {
  padding-left: 32px;
}
.nav-link:hover {
  background-color: var(--color-border);
}
.nav-link.active {
  color: var(--color-primary);
  background-color: var(--color-border);
  font-weight: 600;
}
.nav-icon {
  width: 18px;
  text-align: center;
}
.nav-label {
  flex: 1;
}
</style>
