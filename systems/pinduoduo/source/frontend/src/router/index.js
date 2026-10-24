// 路由配置：拼多多自动回复系统前端路由表
// 整体布局（左侧导航 + 右侧内容 + 顶部面包屑/标签页）由 MainLayout 承载，
// 业务页面作为其子路由渲染于内容区。
//
// 路由生成原则（规范 30/31）：以集中菜单配置 config/navigation.js 为「菜单 + 路由」
// 的唯一数据源，遍历全部叶子菜单项生成路由，保证菜单与路由严格一致；已实现的业务
// 页面经 PAGE_COMPONENTS 按菜单键映射到对应组件，尚未实现的页面回退为统一占位组件。
//
// 鉴权（需求 1.3/1.4）：除登录页等公开路由外，所有页面均需登录后访问；未登录访问
// 受保护路由时重定向到登录页并携带 redirect 回跳参数；已登录再访问登录页则回跳首页。
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/components/layout/MainLayout.vue'
import { getAllLeafItems } from '@/config/navigation'
import { TOKEN_STORAGE_KEY } from '@/api/auth_api'
import { useUserStore } from '@/store'

// 占位组件：尚无对应页面文件的菜单项统一回退到此（极少数情况，保证菜单可点不白屏）。
const PLACEHOLDER = () => import('@/pages/menu_placeholder.vue')

// 已实现页面：菜单键 -> 页面组件（懒加载）。键名须与 config/navigation.js 的菜单 key 一致。
const PAGE_COMPONENTS = {
  platforms: () => import('@/pages/platforms.vue'),
  // —— 主导航（普通用户与管理员均可见，受授权与显隐控制）——
  dashboard: () => import('@/pages/dashboard.vue'),
  'online-chat': () => import('@/pages/online_chat.vue'),
  'harness-operations': () => import('@/pages/harness_operations.vue'),
  shops: () => import('@/pages/shop_management.vue'),
  // 自动回复设置：仅保留全局「关键词规则」菜单；其余店铺级设置已并入店铺管理页弹窗
  keywords: () => import('@/pages/keyword_rules.vue'),
  // 知识库分组
  'product-knowledge': () => import('@/pages/product_knowledge.vue'),
  'service-knowledge': () => import('@/pages/cs_knowledge.vue'),
  // 商品管理
  products: () => import('@/pages/product_management.vue'),
  // 通知分组（通知渠道为店铺级设置，已并入店铺管理弹窗；此处仅保留消息通知）
  'notify-messages': () => import('@/pages/notify_messages.vue'),
  // 日志分组
  'message-logs': () => import('@/pages/message_logs.vue'),
  'risk-logs': () => import('@/pages/risk_logs.vue'),
  // 数据分析
  'data-analysis': () => import('@/pages/data_analysis.vue'),
  // 个人设置（菜单入口；与顶栏「个人设置」/profile 同一页面）
  'personal-settings': () => import('@/pages/profile/personal_settings.vue'),

  // —— 管理端（仅管理员可见）——
  settings: () => import('@/pages/admin/system_settings.vue'),
  'admin-users': () => import('@/pages/admin/user_management.vue'),
  'admin-roles': () => import('@/pages/admin/role_management.vue'),
  'admin-system-logs': () => import('@/pages/system_logs.vue'),
  'admin-scheduled-tasks': () => import('@/pages/admin/scheduled_tasks.vue'),
  'admin-announcements': () => import('@/pages/admin/announcement_management.vue'),
  'admin-feedback': () => import('@/pages/admin/feedback_management.vue'),

  // —— 底部菜单（所有用户）——
  tutorial: () => import('@/pages/tutorial.vue'),
  feedback: () => import('@/pages/feedback.vue'),
  disclaimer: () => import('@/pages/disclaimer.vue'),
  about: () => import('@/pages/about.vue'),
}

// 由集中菜单配置生成业务子路由（菜单与路由同源，规范 30）。
function buildMenuRoutes() {
  return getAllLeafItems()
    .filter((leaf) => leaf.path && leaf.path !== '/')
    .map((leaf) => ({
      // 去除前导斜杠作为子路由 path（MainLayout 的子路由为相对路径）
      path: leaf.path.replace(/^\//, ''),
      name: leaf.key,
      component: PAGE_COMPONENTS[leaf.key] || PLACEHOLDER,
      // meta.title 供面包屑 / 标签页展示；adminOnly / resource 供路由守卫做权限校验
      // （需求 2.5/2.6）：adminOnly 仅管理员可进；resource 需被授予该资源 view 权限。
      meta: {
        title: leaf.label,
        adminOnly: Boolean(leaf.adminOnly),
        resource: leaf.resource || null,
      },
    }))
}

// 路由表：登录页为独立公开路由；业务页面挂在 MainLayout 下并需登录后访问。
const routes = [
  {
    // 登录页（公开，无需鉴权；meta.public 供路由守卫识别）
    path: '/login',
    name: 'login',
    component: () => import('@/pages/auth/login.vue'),
    meta: { title: '登录', public: true },
  },
  {
    // 注册页（公开，无需鉴权）
    path: '/register',
    name: 'register',
    component: () => import('@/pages/auth/register.vue'),
    meta: { title: '注册', public: true },
  },
  {
    path: '/',
    component: MainLayout,
    children: [
      {
        path: '',
        name: 'home',
        // 首页重定向到仪表盘（仪表盘为登录后默认落地页）
        redirect: '/platforms',
      },
      {
        path: 'profile',
        name: 'profile',
        // 顶栏「个人设置」入口（与菜单 personal-settings 同一页面）
        component: () => import('@/pages/profile/personal_settings.vue'),
        meta: { title: '个人设置' },
      },
      // 由集中菜单配置生成的全部业务页面路由
      ...buildMenuRoutes(),
    ],
  },
  {
    // 兜底：未匹配的路径回退首页（由首页守卫决定去登录或仪表盘）
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

// 使用 HTML5 History 模式
const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 计算「无权访问时的安全回退路径」（需求 2.6）。
// 必须保证回退目标本身能通过守卫，否则会无限重定向：
//   - 有 dashboard 资源权限：回退仪表盘（默认落地页）；
//   - 否则：回退个人设置（无 resource 限制、人人可访问，守卫必放行），
//     避免「连仪表盘都无权」的用户陷入 /dashboard 自我重定向死循环。
function resolveFallbackPath(userStore) {
  if (userStore.isAdmin) {
    return '/dashboard'
  }
  const resources = userStore.authorizedResources
  if (Array.isArray(resources) && resources.includes('dashboard')) {
    return '/dashboard'
  }
  return '/personal-settings'
}

// 全局前置守卫：登录态校验 + 菜单/资源级权限校验（需求 1.3/1.4/2.5/2.6/2.9）
// - 公开路由（登录 / 注册）直接放行；已登录访问登录页回跳首页；
// - 未登录访问受保护路由：跳转登录页并携带 redirect 回跳参数；
// - 已登录访问受保护路由：按目标路由 meta 的 adminOnly / resource 校验权限——
//   · adminOnly：仅管理员可进；
//   · resource：管理员强制放行，普通用户需被授予该资源 view 权限；
//   · 无权访问时回退到安全落地页（仪表盘或个人设置），不停留在无权页面。
router.beforeEach(async (to) => {
  // 以 localStorage 中的令牌为登录态判据（与 user_store / request 约定一致）
  const isLoggedIn = Boolean(localStorage.getItem(TOKEN_STORAGE_KEY))

  if (to.meta && to.meta.public) {
    // 已登录则不再停留登录页，回跳首页
    if (isLoggedIn && to.name === 'login') {
      return { path: '/' }
    }
    return true
  }

  if (!isLoggedIn) {
    // 记录原目标路径，登录后回跳（仅站内相对路径）
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    }
  }

  // 已登录：做菜单/资源级权限校验。无 meta 限制的路由（如个人设置）直接放行。
  const meta = to.meta || {}
  if (!meta.adminOnly && !meta.resource) {
    return true
  }

  const userStore = useUserStore()
  // 管理员强制可见全部受保护页面（需求 2.9）。
  if (userStore.isAdmin) {
    return true
  }

  // resource 页面：确保授权资源已加载（刷新页面直达时可能尚未拉取）。
  // adminOnly 页面无需资源，跳过加载。
  if (meta.resource && userStore.authorizedResources === null) {
    try {
      await userStore.loadAuthorizedResources()
    } catch (e) {
      // 拉取失败时放行，交由后端接口判权兜底，避免误锁有权用户
      return true
    }
  }

  // 计算是否有权访问目标页面。
  let allowed
  if (meta.adminOnly) {
    // adminOnly 页面：非管理员（前面已放行管理员）一律拒绝。
    allowed = false
  } else {
    const resources = userStore.authorizedResources
    // 资源未确切加载（非数组）时放行，交后端判权兜底，避免误锁。
    allowed = !Array.isArray(resources) || resources.includes(meta.resource)
  }

  if (allowed) {
    return true
  }

  // 无权：回退安全落地页；若回退目标即当前目标（防御性），放行避免死循环。
  const fallback = resolveFallbackPath(userStore)
  if (fallback === to.path) {
    return true
  }
  return { path: fallback }
})

export default router
