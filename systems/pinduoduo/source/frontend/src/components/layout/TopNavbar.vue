<!-- 顶部导航栏组件：拼多多自动回复系统前端 -->
<!-- 风格与参考项目 xianyu-auto-reply-wangpan 一致：左侧欢迎语，右侧暗黑切换 + 主题色 + 用户菜单。 -->
<!-- 功能： -->
<!--   1. 左侧：欢迎使用「系统名称」（移动端为系统名称）； -->
<!--   2. 右侧：暗黑模式切换、主题色选择（本项目支持主题色修改，规范 26）、用户信息与菜单（规范 25）。 -->
<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Sun, Moon, Palette, ChevronDown, LogOut, UserCog } from 'lucide-vue-next'
import { useLayoutStore, useUserStore } from '@/store'
import { THEME_COLOR_PRESETS } from '@/utils/theme'
import { authApi } from '@/api'
import { version } from '../../../package.json'

const router = useRouter()
const uiStore = useLayoutStore()
const userStore = useUserStore()

const showUserMenu = ref(false)
const showColorPanel = ref(false)

const isDark = computed(() => uiStore.darkMode)
const themeColor = computed(() => uiStore.themeColor)
const colorPresets = THEME_COLOR_PRESETS

// 系统名称（注入欢迎语）
defineProps({
  systemName: {
    type: String,
    default: 'OIN',
  },
})

// 当前用户展示名（昵称优先，其次用户名，最后占位「用户」）
const displayName = computed(() => {
  const info = userStore.userInfo
  return (info && (info.nickname || info.username)) || '用户'
})
const roleLabel = computed(() => (userStore.isAdmin ? '管理员' : '普通用户'))

function onToggleDark() {
  uiStore.toggleDarkMode()
}

function onSelectColor(key) {
  uiStore.setThemeColor(key)
  showColorPanel.value = false
}

function goProfile() {
  showUserMenu.value = false
  router.push('/personal-settings')
}

// 退出登录（需求 1.5）：调用后端使令牌失效，随后清除本地用户并跳转登录页。
async function onLogout() {
  showUserMenu.value = false
  try {
    await authApi.logout()
  } catch (e) {
    // 忽略登出接口异常，确保前端始终能清理本地登录态
  }
  userStore.clearUser()
  router.push('/login')
}
</script>

<template>
  <div class="top-navbar">
    <!-- 左侧：欢迎语（移动端为系统名称，为移动端汉堡按钮留出左边距） -->
    <div class="flex items-center gap-3 ml-12 sm:ml-0 flex-1 min-w-0">
      <span class="text-sm text-slate-500 dark:text-slate-400 hidden sm:inline max-w-[320px] truncate">
        {{ systemName }} <small class="ml-2 text-xs text-slate-400">v{{ version }}</small>
      </span>
      <span class="text-sm text-slate-500 dark:text-slate-400 sm:hidden max-w-[140px] truncate">
        {{ systemName }} <small class="ml-2 text-xs text-slate-400">v{{ version }}</small>
      </span>
    </div>

    <!-- 右侧：工具栏 -->
    <div class="flex items-center gap-1 sm:gap-2">
      <!-- 暗黑模式切换 -->
      <button
        type="button"
        class="p-2 rounded-md text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700
               hover:text-slate-700 dark:hover:text-slate-200 transition-colors duration-150"
        :title="isDark ? '切换到亮色模式' : '切换到暗色模式'"
        @click="onToggleDark"
      >
        <Sun v-if="isDark" class="w-5 h-5" />
        <Moon v-else class="w-5 h-5" />
      </button>

      <!-- 主题色选择 -->
      <div class="relative">
        <button
          type="button"
          class="p-2 rounded-md text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700
                 hover:text-slate-700 dark:hover:text-slate-200 transition-colors duration-150"
          title="主题颜色"
          @click="showColorPanel = !showColorPanel"
        >
          <Palette class="w-5 h-5" />
        </button>
        <template v-if="showColorPanel">
          <div class="fixed inset-0 z-40" @click="showColorPanel = false" />
          <div
            class="absolute right-0 mt-2 w-44 bg-white dark:bg-slate-800 rounded-lg shadow-lg
                   ring-1 ring-black/5 dark:ring-white/10 p-3 z-50 animate-fade-in"
          >
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-2">主题颜色</p>
            <div class="flex flex-wrap gap-2.5">
              <button
                v-for="preset in colorPresets"
                :key="preset.key"
                type="button"
                class="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110"
                :class="preset.key === themeColor ? 'border-slate-800 dark:border-white' : 'border-transparent'"
                :style="{ background: preset.color }"
                :title="preset.label"
                @click="onSelectColor(preset.key)"
              />
            </div>
          </div>
        </template>
      </div>

      <!-- 用户菜单 -->
      <div class="relative">
        <button
          type="button"
          class="flex items-center gap-1 sm:gap-2 px-2 sm:px-3 py-1.5 rounded-md text-slate-700 dark:text-slate-200
                 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors duration-150"
          @click="showUserMenu = !showUserMenu"
        >
          <div class="w-7 h-7 rounded-full bg-primary-500 flex items-center justify-center text-white text-xs font-medium">
            {{ displayName.charAt(0).toUpperCase() }}
          </div>
          <span class="text-sm font-medium hidden sm:inline max-w-[120px] truncate">{{ displayName }}</span>
          <ChevronDown class="w-4 h-4 text-slate-400 hidden sm:block" />
        </button>

        <template v-if="showUserMenu">
          <div class="fixed inset-0 z-40" @click="showUserMenu = false" />
          <div
            class="absolute right-0 mt-2 w-48 bg-white dark:bg-slate-800 rounded-lg shadow-lg
                   ring-1 ring-black/5 dark:ring-white/10 py-1 z-50 animate-fade-in"
          >
            <div class="px-4 py-2 border-b border-slate-100 dark:border-slate-700">
              <p class="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">{{ displayName }}</p>
              <p class="text-xs text-slate-500 dark:text-slate-400">{{ roleLabel }}</p>
            </div>
            <button
              type="button"
              class="w-full flex items-center gap-2 px-4 py-2 text-sm text-slate-700 dark:text-slate-300
                     hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors duration-150"
              @click="goProfile"
            >
              <UserCog class="w-4 h-4" />
              个人设置
            </button>
            <button
              type="button"
              class="w-full flex items-center gap-2 px-4 py-2 text-sm text-red-600 dark:text-red-400
                     hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors duration-150"
              @click="onLogout"
            >
              <LogOut class="w-4 h-4" />
              退出登录
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
