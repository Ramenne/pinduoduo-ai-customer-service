<script setup>
import { computed } from 'vue'
const props = defineProps({ text: String })
// 只格式化常见的加粗标记，所有内容通过Vue文本插值输出，不执行模型HTML。
const parts = computed(() => (props.text || '').split(/(\*\*[^*\n]+\*\*)/g).map((text, id) => ({
  id, bold: text.startsWith('**') && text.endsWith('**'),
  text: text.startsWith('**') && text.endsWith('**') ? text.slice(2, -2) : text,
})))
</script>
<template><p><template v-for="part in parts" :key="part.id"><strong v-if="part.bold">{{ part.text }}</strong><template v-else>{{ part.text }}</template></template></p></template>
