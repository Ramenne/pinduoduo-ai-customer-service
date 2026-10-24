<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store'
import { fetchPlatforms } from '@/api/platform_api'

const router = useRouter(), user = useUserStore()
const platforms = ref([]), loading = ref(false), failed = ref(false)
async function load() {
  if (loading.value) return
  loading.value = true; failed.value = false
  try { platforms.value = (await fetchPlatforms()).list }
  catch { failed.value = true }
  finally { loading.value = false }
}
const canChat = () => user.isAdmin || user.authorizedResources?.includes('chat')
onMounted(load)
</script>

<template>
  <section class="platform-page">
    <header class="platform-header">
      <div><h1>平台与账号</h1><p>选择平台，管理账号或进入客服工作台</p></div>
      <button class="btn-ios-secondary" :disabled="loading" @click="load">{{ loading ? '正在读取…' : '刷新' }}</button>
    </header>
    <p v-if="failed" class="platform-error" role="alert">平台信息未能读取，请刷新重试。</p>
    <div class="platform-grid" :aria-busy="loading">
      <article v-for="platform in platforms" :key="platform.code" class="platform-card">
        <div class="platform-card-head"><h2>{{ platform.name }}</h2><span :class="platform.mode === 'integrated' ? 'integrated' : 'external'">{{ platform.mode === 'integrated' ? '已接入' : '独立后台' }}</span></div>
        <template v-if="platform.mode === 'integrated'">
          <p class="platform-count"><strong>{{ platform.account_count }}</strong> 个可管理账号</p>
          <p>账号授权和消息连接由服务器处理。</p>
          <div class="platform-actions">
            <button class="btn-ios-primary" @click="router.push(platform.accounts_path)">管理账号</button>
            <button v-if="canChat()" class="btn-ios-secondary" @click="router.push(platform.chat_path)">进入客服</button>
          </div>
        </template>
        <template v-else>
          <p class="platform-external-note">使用现有闲鱼后台，账号与会话尚未合并到 OIN。</p>
          <p>需要使用该后台的账号登录。</p>
          <div class="platform-actions"><a class="btn-ios-secondary" :href="platform.external_url" target="_blank" rel="noopener noreferrer">打开闲鱼后台 ↗</a></div>
        </template>
      </article>
    </div>
  </section>
</template>

<style scoped>
.platform-page{max-width:1160px;margin:0 auto;color:var(--text-color,#26354b)}
.platform-header,.platform-card-head{display:flex;align-items:center;justify-content:space-between;gap:16px}
.platform-header{margin-bottom:24px}.platform-header h1{font-size:22px;margin:0 0 8px;font-weight:600}
.platform-header p,.platform-card p{font-size:13px;line-height:1.8;color:var(--text-secondary,#7a8799);margin:0}
.platform-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.platform-card{padding:24px;border:1px solid var(--border-color,#e2e8f0);border-radius:14px;background:var(--bg-color,#fff);display:flex;flex-direction:column;gap:16px;min-width:0}
.platform-card h2{font-size:18px;font-weight:600;margin:0}.platform-card-head span{font-size:11px;border-radius:6px;padding:4px 8px}
.integrated{background:#eaf8ef;color:#278153}.external{background:#f0f3f7;color:#6b798e}
.platform-count strong{font-size:30px;line-height:1.4;font-weight:600;color:var(--text-color,#26354b);margin-right:8px}
.platform-external-note{min-height:42px}.platform-actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:auto;padding-top:8px}
.platform-actions a{text-decoration:none}.platform-error{padding:14px;border-radius:8px;background:#fff6e9;color:#9b631c;margin-bottom:16px;font-size:13px}
@media(max-width:700px){.platform-grid{grid-template-columns:1fr}.platform-card{padding:20px}.platform-header h1{font-size:20px}}
</style>
