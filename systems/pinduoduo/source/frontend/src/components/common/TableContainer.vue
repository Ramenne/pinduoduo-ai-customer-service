<!--
  表格固定高度内部滚动容器（TableContainer）
  职责：为查询表格提供固定高度 + 内部滚动的通用容器，使滚动条出现在表格内部，
        尽量避免浏览器窗口整体出现滚动条（规范 29 / 需求 23.8）。
  用法：
    <TableContainer>
      <table> ... </table>
    </TableContainer>
  可通过 max-height / height 属性自定义高度；默认填满父容器可用空间。
-->
<script setup>
defineProps({
  // 容器最大高度（如 '60vh'、'480px'）；默认 100% 由父级 flex 约束
  maxHeight: {
    type: String,
    default: '100%',
  },
})
</script>

<template>
  <div class="table-container" :style="{ maxHeight }">
    <!-- 表格等内容放入插槽，超出高度时容器内部滚动 -->
    <slot />
  </div>
</template>

<style scoped>
.table-container {
  width: 100%;
  height: 100%;
  /* 内部滚动：纵向超出时容器内滚动，避免窗口整体滚动 */
  overflow: auto;
  border: 1px solid var(--color-border, #e5e6eb);
  border-radius: 8px;
  background: var(--color-bg, #ffffff);
}

:where(.table-container) :deep(table) {
  min-width: 760px;
}

/* 表头吸顶，滚动时保持可见（配合内部使用的 table） */
.table-container :deep(thead th) {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--color-bg, #ffffff);
}
</style>
