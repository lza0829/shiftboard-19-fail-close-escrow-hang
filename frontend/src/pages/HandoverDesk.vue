<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const fromId = ref(1)
const toId = ref(2)
const current = ref(null)
const msg = ref('')
const ok = ref(false)

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  const logs = await getJson('/api/handovers')
  current.value = logs.find((h) => h.status === '进行中') || null
  if (current.value) {
    current.value = await getJson(`/api/handovers/${current.value.id}`)
  }
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}，按发起时刻快照清单 ${current.value.batch_count} 批，已整批入托管`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function complete() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson(`/api/handovers/${current.value.id}/complete`)
    ok.value = current.value.status === '已完成'
    msg.value = current.value.status === '已完成'
      ? `交班单 #${current.value.id} 已完成，托管批次已整批过户`
      : `交班单 #${current.value.id} 失败关闭：${current.value.reason || '归属不一致'}`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function failClose() {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    const reason = window.prompt('请输入失败关闭原因', '接班核验不过')
    if (reason === null) return
    current.value = await postJson(`/api/handovers/${current.value.id}/fail`, { reason })
    ok.value = false
    msg.value = `交班单 #${current.value.id} 已标失败`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">同一时刻只允许一单进行中的交班；发起即按当前在途重快照并整批入托管，失败关闭同事务解冻、托管归位，失败单禁止续跑，只能重开新单。</p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}（{{ s.status }}）</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}（{{ s.status }}）</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current" @click="start">发起交班</button>
        <button class="btn ghost" :disabled="!current" @click="complete">完成交班</button>
        <button class="btn" style="background:#b3261e;color:#fff" :disabled="!current" @click="failClose">失败关闭</button>
      </div>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
    </div>

    <div class="panel" v-if="current">
      <h2>当前交班单 #{{ current.id }} · {{ current.status }}</h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} ·
        清单 {{ current.batch_count }} 批 · 托管中 {{ current.escrow_items?.length || 0 }} 批
      </p>
      <table>
        <thead><tr><th>业务键（快照）</th><th>标题</th><th>当前状态</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.snapshot_biz_key || b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td><span class="tag" :class="{ on: b.status === '托管中' }">{{ b.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
