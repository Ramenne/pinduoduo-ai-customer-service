<!--
  使用教程页面（需求 21.5）
  职责：向用户展示系统主要功能的使用指引（静态内容，中文）。
  规范要点：全中文（规范 27）、响应式（规范 20）、背景与文字对比清晰（规范 24）。
-->
<script setup>
// 教程章节（静态内容，便于后续扩展为后端可配置）
const sections = [
  {
    title: '一、接入店铺',
    steps: [
      '进入「店铺管理」，通过账号密码登录或粘贴 Cookie 导入接入拼多多店铺。',
      '接入成功后可在列表查看店铺登录态与连接状态。',
    ],
  },
  {
    title: '二、配置自动回复',
    steps: [
      '在「关键词规则」中配置常见问题的关键词与回复内容。',
      '在「默认与商品专属回复」中配置兜底回复与特定商品的专属回复。',
      '在「AI 设置」中启用 AI 智能回复并配置大模型参数与知识库。',
    ],
  },
  {
    title: '三、营业时间与风控',
    steps: [
      '在「营业时间」中设置自动回复生效的时间区间。',
      '在「风控管理」中配置回复频率上限，降低账号风险。',
    ],
  },
  {
    title: '四、查看日志与数据',
    steps: [
      '在「消息日志」「风控日志」中追溯处理过程。',
      '在「仪表盘」「数据分析」中掌握运营概览与趋势。',
    ],
  },
]
</script>

<template>
  <div class="tutorial-page">
    <h2 class="tutorial-page__title">使用教程</h2>
    <section v-for="section in sections" :key="section.title" class="card">
      <h3 class="card__title">{{ section.title }}</h3>
      <ol class="step-list">
        <li v-for="(step, index) in section.steps" :key="index" class="step-item">{{ step }}</li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.tutorial-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.tutorial-page__title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}
.card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 16px;
}
.card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 10px;
}
.step-list {
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.step-item {
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-text);
}
</style>
