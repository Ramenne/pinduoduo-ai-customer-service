// 前端应用入口：创建 Vue 3 应用实例，挂载 Pinia 状态管理与 Vue Router 路由
// 本文件仅负责装配应用骨架，具体业务在各页面/组件中实现
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/index.css'
import { useLayoutStore } from './store'

// 创建应用实例
const app = createApp(App)

// 注册状态管理（Pinia）与路由（Vue Router）
const pinia = createPinia()
app.use(pinia)
app.use(router)

// 应用挂载前初始化主题（暗黑模式 + 主题色），与 index.html 提前注入的暗黑判断保持一致，避免闪烁
useLayoutStore(pinia).initTheme()

// 挂载到 index.html 的 #app 节点
app.mount('#app')
