<!--
  XSS 安全渲染组件（SafeHtml）
  职责：对用户输入内容进行转义/过滤后再渲染，避免 v-html 注入未经处理的数据（规范 22 / 需求 23.10）。
  两种模式：
    - text 模式（默认）：对内容整体 HTML 转义后作为纯文本展示，绝不解析任何标记，最安全；
    - html 模式：基于白名单清洗（sanitizeHtml）后经 v-html 渲染，用于确需保留部分标签的富文本展示。
  用法：
    <SafeHtml :content="userInput" />              纯文本安全展示
    <SafeHtml :content="richText" mode="html" />   富文本白名单清洗后展示
-->
<script setup>
import { computed } from 'vue'
import { escapeHtml, sanitizeHtml } from '@/utils/safe_html'

const props = defineProps({
  // 待渲染内容（用户输入）
  content: {
    type: [String, Number],
    default: '',
  },
  // 渲染模式：text（纯文本转义）/ html（白名单清洗后富文本）
  mode: {
    type: String,
    default: 'text',
  },
})

// html 模式经白名单清洗；text 模式整体转义为纯文本
const rendered = computed(() => {
  if (props.mode === 'html') {
    return sanitizeHtml(props.content)
  }
  return escapeHtml(props.content)
})
</script>

<template>
  <!-- 两种模式均渲染已处理后的安全字符串，不直接渲染原始用户输入 -->
  <div class="safe-html" v-html="rendered"></div>
</template>

<style scoped>
.safe-html {
  word-break: break-word;
  line-height: 1.6;
}

.safe-html :deep(a) {
  color: var(--color-primary, #1677ff);
  text-decoration: none;
}

.safe-html :deep(a:hover) {
  text-decoration: underline;
}
</style>
