// 通用组件统一导出（common）
// 用法：import { Toast, Loading, ConfirmModal, SafeHtml, Select, TableContainer, Pagination, FormModal, ShopSelector } from '@/components/common'
// 这些组件覆盖：Toast 替代 alert（规范 6）、确认弹窗/表单弹窗仅关闭按钮关闭（规范 7）、
// 加载遮罩 + 转圈（规范 23）、XSS 安全渲染（规范 22）、固定高度内部滚动表格容器（规范 29）、
// 后端分页控件（规范 28）、店铺选择器（按店铺维度配置复用，规范 36）。
export { default as Toast } from './Toast.vue'
export { default as Loading } from './Loading.vue'
export { default as ConfirmModal } from './ConfirmModal.vue'
export { default as SafeHtml } from './SafeHtml.vue'
export { default as Select } from './Select.vue'
export { default as TableContainer } from './TableContainer.vue'
export { default as Pagination } from './Pagination.vue'
export { default as FormModal } from './FormModal.vue'
// 店铺选择器（按店铺维度配置复用，规范 36）
export { default as ShopSelector } from './ShopSelector.vue'
// 登录滑块拼图验证码（登录前人机校验，可由系统设置开关，规范 6/20/22）
export { default as SliderCaptcha } from './SliderCaptcha.vue'
