// 导航/菜单集中配置（拼多多自动回复系统前端）
// 职责（规范 30/31、需求 2.5/2.6/2.9）：
//   1. 作为「菜单 + 路由」的唯一数据源（单一事实来源）：左侧导航、顶部导航、Vue Router 路由
//      均由本文件集中配置派生，确保菜单与路由严格一致（规范 30：集中配置与路由二者择一并保证一致）；
//   2. 定义每个菜单项的菜单键(key)、中文标题(label)、路由(path)、图标(icon)、
//      是否管理员专属(adminOnly)、父子层级(children)；
//   3. 提供按「用户被授权菜单 + 管理员强制可见专属菜单 + 隐藏菜单」过滤可见菜单的纯函数，
//      供侧边栏(Sidebar)与顶部导航(TopNavbar)复用。
// 说明：icon 字段为 lucide 图标名称（见 components/layout/AppIcon.vue 的映射表），
//      与参考项目的 lucide 线性图标风格保持一致；不影响本文件的菜单/权限逻辑。

// 菜单配置（数组顺序即展示顺序）
// 字段说明：
//   key       菜单键（全局唯一，权限以此为准，亦登记到数据字典「菜单键」类型）
//   label     菜单中文标题（规范 27 全中文）
//   path      路由路径（叶子菜单必填；分组菜单不含 path，仅含 children）
//   icon      菜单图标（emoji 占位）
//   adminOnly 是否仅管理员可见（需求 2.5/2.9）
//   resource  菜单对应的权限资源键（按资源 view 授权控制显隐）
//   children  子菜单数组（分组菜单）
// ---------------------------------------------------------------------------

// 主导航菜单（普通用户与管理员均可见，受授权控制）
export const mainNavItems = [
  { key: 'platforms', icon: 'shops', label: '平台与账号', path: '/platforms', resource: 'shop' },
  { key: 'dashboard', icon: 'dashboard', label: '仪表盘', path: '/dashboard', resource: 'dashboard' },
  { key: 'online-chat', icon: 'online-chat', label: '在线聊天', path: '/online-chat', resource: 'chat' },
  { key: 'harness-operations', icon: 'dashboard', label: '运行状态与经验', path: '/harness-operations', resource: 'chat' },
  { key: 'shops', icon: 'shops', label: '店铺管理', path: '/shops', resource: 'shop' },
  // 关键词规则为全局规则配置，保留独立菜单；其余「按店铺维度」的自动回复设置
  // （默认与商品专属回复、AI 设置、营业时间、消息过滤与黑名单、风控管理、转人工设置）
  // 已统一收敛到「店铺管理」页的行内「设置」弹窗中配置，不再单独开菜单。
  { key: 'keywords', icon: 'keywords', label: '关键词规则', path: '/auto-reply/keywords', resource: 'keyword' },
  {
    key: 'knowledge',
    icon: 'knowledge',
    label: '知识库',
    children: [
      { key: 'product-knowledge', icon: 'product-knowledge', label: '商品知识库', path: '/knowledge/products', resource: 'product_knowledge' },
      { key: 'service-knowledge', icon: 'service-knowledge', label: '客服知识库', path: '/knowledge/service', resource: 'cs_knowledge' },
    ],
  },
  { key: 'products', icon: 'products', label: '商品管理', path: '/products', resource: 'product' },
  {
    key: 'notify',
    icon: 'notify',
    label: '通知',
    children: [
      // 通知渠道为店铺级设置，已并入「店铺管理」页弹窗配置，不再单独开菜单；
      // 此处仅保留全局的「消息通知」记录查看菜单。
      { key: 'notify-messages', icon: 'notify-messages', label: '消息通知', path: '/notify/messages', resource: 'notify' },
    ],
  },
  {
    key: 'logs',
    icon: 'logs',
    label: '日志',
    children: [
      { key: 'message-logs', icon: 'message-logs', label: '消息日志', path: '/logs/messages', resource: 'message_log' },
      { key: 'risk-logs', icon: 'risk-logs', label: '风控日志', path: '/logs/risk', resource: 'risk_log' },
    ],
  },
  { key: 'data-analysis', icon: 'data-analysis', label: '数据分析', path: '/data-analysis', resource: 'dashboard' },
  { key: 'personal-settings', icon: 'personal-settings', label: '个人设置', path: '/personal-settings', resource: 'profile' },
]

// 管理端菜单（仅管理员可见；对管理员强制可见，需求 2.5/2.9）
// 管理端菜单（管理员可见；部分管理端能力可经资源授权开放给被授权的非管理员）
// 说明（需求 2.5/2.6/2.9）：
//   - adminOnly=true：仅管理员可见（后端按 is_admin 判权的功能：系统设置 / 定时任务 /
//     公告 / 意见反馈管理）；
//   - resource=xxx：后端按资源键判权的功能（用户 / 角色 / 系统日志），被授予该资源
//     view 权限的非管理员也可见其入口，与后端 permission.check 判权保持一致；管理员
//     强制可见全部（需求 2.9）。
export const adminNavItems = [
  { key: 'settings', icon: 'settings', label: '系统设置', path: '/settings', adminOnly: true },
  { key: 'admin-users', icon: 'admin-users', label: '用户管理', path: '/admin/users', resource: 'user' },
  { key: 'admin-roles', icon: 'admin-roles', label: '角色权限', path: '/admin/roles', resource: 'role' },
  { key: 'admin-system-logs', icon: 'admin-system-logs', label: '系统日志', path: '/admin/system-logs', resource: 'system_log' },
  { key: 'admin-scheduled-tasks', icon: 'admin-scheduled-tasks', label: '定时任务', path: '/admin/scheduled-tasks', adminOnly: true },
  { key: 'admin-announcements', icon: 'admin-announcements', label: '公告管理', path: '/admin/announcements', adminOnly: true },
  { key: 'admin-feedback', icon: 'admin-feedback', label: '意见反馈', path: '/admin/feedback', adminOnly: true },
]

// 底部菜单（教程、反馈、免责声明、关于；统一纳入权限控制，按角色授权显隐）
export const bottomNavItems = [
  { key: 'tutorial', icon: 'tutorial', label: '使用教程', path: '/tutorial', resource: 'tutorial' },
  { key: 'feedback', icon: 'feedback', label: '意见反馈', path: '/feedback', resource: 'feedback' },
  { key: 'disclaimer', icon: 'disclaimer', label: '免责声明', path: '/disclaimer', resource: 'disclaimer' },
  { key: 'about', icon: 'about', label: '关于', path: '/about', resource: 'about' },
]

// 全部菜单分区（按展示顺序）
export const allNavSections = [...mainNavItems, ...adminNavItems, ...bottomNavItems]

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

// 判断某菜单项是否为分组（含 children）
export function isNavGroup(entry) {
  return Array.isArray(entry && entry.children)
}

// 路径前缀匹配：currentPath === targetPath 或 currentPath 以 `${targetPath}/` 开头
function matchesPath(currentPath, targetPath) {
  if (!currentPath || !targetPath) {
    return false
  }
  return currentPath === targetPath || currentPath.startsWith(`${targetPath}/`)
}

// 提取所有叶子菜单项（含 path 的项），供路由生成与可隐藏菜单列表使用
export function getAllLeafItems(entries = allNavSections) {
  const leaves = []
  for (const entry of entries) {
    if (isNavGroup(entry)) {
      for (const child of entry.children) {
        leaves.push(child)
      }
      continue
    }
    if (entry.path) {
      leaves.push(entry)
    }
  }
  return leaves
}

// 判断某菜单项是否被当前用户授权（基于资源键，需求 2.6）：
//   - 管理员：始终授权（强制可见，需求 2.9）；
//   - 菜单无 resource：对所有登录用户开放（如个人设置），始终授权；
//   - authorizedResources 为 null/undefined：视为不限制（授权信息尚未加载时全部放行，
//     避免登录初始化阶段菜单闪烁为空）；
//   - 否则：以授权资源集合是否包含该菜单所需资源为准。
function isMenuAuthorized(entry, isAdmin, authorizedResources) {
  if (isAdmin) {
    return true
  }
  // 无 resource 的菜单（个人设置等）对所有登录用户开放
  if (!entry.resource) {
    return true
  }
  if (authorizedResources === null || authorizedResources === undefined) {
    return true
  }
  return authorizedResources.includes(entry.resource)
}

// 判断某叶子/一级菜单项对当前用户是否可见（综合：管理员专属、授权）
function isEntryVisible(entry, { isAdmin = false, authorizedResources = null } = {}) {
  // 管理员专属菜单：仅管理员可见（需求 2.5）；对管理员强制可见（需求 2.9）
  if (entry.adminOnly) {
    return Boolean(isAdmin)
  }
  // 管理员：非专属菜单一律可见（不受授权限制，需求 2.9 强制可见语义）
  if (isAdmin) {
    return true
  }
  // 普通用户：仅渲染被授权（按资源键）的菜单（需求 2.6）
  return isMenuAuthorized(entry, false, authorizedResources)
}

// 计算可见的菜单分区（保留层级结构）：
//   - 过滤掉对当前用户不可见的一级菜单/分组；
//   - 分组菜单按其子项可见性过滤 children，无可见子项的分组整体隐藏。
// 参数 options：{ isAdmin, authorizedResources }
export function getVisibleNavEntries(entries, options = {}) {
  const result = []
  for (const entry of entries) {
    if (isNavGroup(entry)) {
      // 分组本身受管理员专属限制
      if (entry.adminOnly && !options.isAdmin) {
        continue
      }
      const visibleChildren = entry.children.filter((child) => isEntryVisible(child, options))
      if (visibleChildren.length > 0) {
        result.push({ ...entry, children: visibleChildren })
      }
      continue
    }
    if (isEntryVisible(entry, options)) {
      result.push(entry)
    }
  }
  return result
}

// 便捷封装：主导航可见项
export function getVisibleMainNavItems(options = {}) {
  return getVisibleNavEntries(mainNavItems, options)
}

// 便捷封装：管理端可见项
export function getVisibleAdminNavItems(options = {}) {
  return getVisibleNavEntries(adminNavItems, options)
}

// 便捷封装：底部菜单可见项
export function getVisibleBottomNavItems(options = {}) {
  return getVisibleNavEntries(bottomNavItems, options)
}

// 根据路由路径定位其所属的一级菜单项/分组（用于顶部导航高亮与面包屑，规范 31）
export function getTopLevelMenuEntryByPath(path) {
  for (const entry of [...mainNavItems, ...adminNavItems]) {
    if (isNavGroup(entry)) {
      if (entry.children.some((child) => matchesPath(path, child.path))) {
        return entry
      }
      continue
    }
    if (matchesPath(path, entry.path)) {
      return entry
    }
  }
  for (const item of bottomNavItems) {
    if (matchesPath(path, item.path)) {
      return item
    }
  }
  return null
}

// 根据路由路径定位匹配的叶子菜单项（用于面包屑末级与标签页标题，规范 31）
export function getLeafMenuEntryByPath(path) {
  const leaves = getAllLeafItems()
  // 优先精确匹配，其次前缀匹配（取最长 path 命中，避免父路径误匹配）
  let matched = null
  for (const leaf of leaves) {
    if (matchesPath(path, leaf.path)) {
      if (!matched || leaf.path.length > matched.path.length) {
        matched = leaf
      }
    }
  }
  return matched
}

// 计算某路由路径的面包屑链（[一级菜单, 叶子菜单]），供 TopNavbar 渲染（规范 31）
export function getBreadcrumbByPath(path) {
  const crumbs = []
  const topLevel = getTopLevelMenuEntryByPath(path)
  if (topLevel) {
    crumbs.push({ key: topLevel.key, label: topLevel.label })
  }
  const leaf = getLeafMenuEntryByPath(path)
  // 叶子与一级不同（分组下的子项）时追加叶子层级
  if (leaf && (!topLevel || leaf.key !== topLevel.key)) {
    crumbs.push({ key: leaf.key, label: leaf.label })
  }
  return crumbs
}
