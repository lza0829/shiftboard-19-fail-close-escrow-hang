<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const expanded = ref(null)
const detail = ref(null)
const err = ref('')

async function toggle(h) {
  if (expanded.value === h.id) {
    expanded.value = null
    detail.value = null
    return
  }
  expanded.value = h.id
  detail.value = await getJson(`/api/handovers/${h.id}`)
}

onMounted(async () => {
  try {
    rows.value = await getJson('/api/handovers')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>交班记录</h1>
    <p class="lead">每单交班留档：失败单与重开新单都保留；展开可看独立清单、关闭快照（锁释放/托管归位）与不可变状态机事件。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>单号</th><th>交班方</th><th>接班方</th><th>清单批次数</th><th>状态</th>
            <th>锁释放</th><th>托管归位</th><th>开始</th><th>关闭</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in rows" :key="h.id">
            <td>#{{ h.id }}</td>
            <td>{{ h.from_shift_name }}</td>
            <td>{{ h.to_shift_name }}</td>
            <td>{{ h.batch_count }}</td>
            <td><span class="tag" :class="{ on: h.status === '已完成' }">{{ h.status }}</span></td>
            <td>{{ h.status === '进行中' ? '—' : (h.released_lock ? '已释放' : '未释放') }}</td>
            <td>{{ h.status === '进行中' ? '—' : h.escrow_returned_count }}</td>
            <td>{{ h.started_at }}</td>
            <td>{{ h.closed_at || '—' }}</td>
            <td><button class="btn ghost" @click="toggle(h)">{{ expanded === h.id ? '收起' : '事件' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="detail && expanded !== null">
      <h2>交班单 #{{ expanded }} 明细</h2>
      <p v-if="detail.closure_reason" class="msg">关闭原因：{{ detail.closure_reason }}</p>
      <h3 style="margin:12px 0 6px">清单快照（独立清单表，不可复用）</h3>
      <table>
        <thead><tr><th>业务键快照</th><th>标题</th><th>当前状态</th></tr></thead>
        <tbody>
          <tr v-for="b in detail.items" :key="b.id">
            <td>{{ b.snapshot_biz_key || b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin:12px 0 6px">状态机事件（不可变）</h3>
      <table>
        <thead><tr><th>顺序</th><th>事件</th><th>时间</th><th>载荷</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in detail.events" :key="e.id">
            <td>{{ i + 1 }}</td>
            <td>{{ e.event_type }}</td>
            <td>{{ e.created_at }}</td>
            <td style="white-space:pre-wrap;font-family:monospace">{{ JSON.stringify(e.payload) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
