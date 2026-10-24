<!-- 通用图标组件：基于 lucide-vue-next 的线性图标（与参考项目 lucide 风格一致） -->
<!-- 说明： -->
<!--   1. 通过 name 选择图标，currentColor 跟随文字颜色，保证暗黑模式与主题色一致； -->
<!--   2. 集中维护「名称 -> lucide 组件」映射，菜单与布局统一引用名称，未命中回退到圆点图标； -->
<!--   3. 替代旧的 emoji 图标，使整体风格与参考项目（lucide 线性图标）统一。 -->
<script setup>
import { computed } from 'vue'
import {
  LayoutDashboard,
  MessageSquare,
  Store,
  Bot,
  KeyRound,
  Reply,
  Sparkles,
  Clock,
  Filter,
  Shield,
  UserCheck,
  BookOpen,
  Package,
  PackageSearch,
  ShoppingCart,
  Bell,
  Radio,
  Mail,
  ScrollText,
  FileText,
  AlertTriangle,
  BarChart3,
  User,
  Settings,
  Users,
  Lock,
  ClipboardList,
  Timer,
  Megaphone,
  MessageSquarePlus,
  Info,
  Menu,
  X,
  Sun,
  Moon,
  Palette,
  ChevronDown,
  LogOut,
  PanelLeftClose,
  PanelLeft,
  RefreshCw,
  Save,
  Circle,
} from 'lucide-vue-next'

const props = defineProps({
  // 图标名称
  name: {
    type: String,
    required: true,
  },
  // 图标尺寸（像素）
  size: {
    type: [Number, String],
    default: 18,
  },
})

// 名称 -> lucide 图标组件映射（菜单键与布局图标统一在此登记）
const ICONS = {
  // ===== 菜单图标 =====
  dashboard: LayoutDashboard,
  'online-chat': MessageSquare,
  shops: Store,
  'auto-reply': Bot,
  keywords: KeyRound,
  'default-reply': Reply,
  'ai-settings': Sparkles,
  'business-hours': Clock,
  'message-filters': Filter,
  'risk-control': Shield,
  transfer: UserCheck,
  knowledge: BookOpen,
  'product-knowledge': Package,
  'service-knowledge': BookOpen,
  products: ShoppingCart,
  notify: Bell,
  'notify-channels': Radio,
  'notify-messages': Mail,
  logs: ScrollText,
  'message-logs': FileText,
  'risk-logs': Shield,
  'data-analysis': BarChart3,
  'personal-settings': User,
  settings: Settings,
  'admin-users': Users,
  'admin-roles': Lock,
  'admin-system-logs': ClipboardList,
  'admin-scheduled-tasks': Timer,
  'admin-announcements': Megaphone,
  'admin-feedback': MessageSquarePlus,
  tutorial: BookOpen,
  feedback: MessageSquarePlus,
  disclaimer: AlertTriangle,
  about: Info,
  // ===== 布局/操作图标 =====
  menu: Menu,
  close: X,
  sun: Sun,
  moon: Moon,
  palette: Palette,
  chevronDown: ChevronDown,
  user: User,
  logout: LogOut,
  collapse: PanelLeftClose,
  expand: PanelLeft,
  refresh: RefreshCw,
  save: Save,
}

// 当前图标组件（未命中回退到圆点，避免渲染空白）
const iconComponent = computed(() => ICONS[props.name] || Circle)
</script>

<template>
  <component :is="iconComponent" :size="Number(size)" :stroke-width="2" aria-hidden="true" />
</template>
