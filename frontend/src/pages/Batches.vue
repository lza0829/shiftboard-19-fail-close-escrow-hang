<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const err = ref('')
const busy = ref(null)

async function refresh() {
  rows.value = await getJson('/api/batches')
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function act(id, path) {
  err.value = ''
  busy.value = id
  try {
    await postJson(`/api/batches/${id}/${path}`)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div>
    <h1>在途批次</h1>
    <p class="lead">在途批次归属当前值班班次；交班发起后整批入托管，托管中禁止结清/冻结；结清或冻结的批不进新交班快照，解冻后回到在途可立即再交。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>业务键</th><th>标题</th><th>归属班次</th><th>状态</th><th>备注</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.owner_shift_name || '—' }}</td>
            <td><span class="tag" :class="{ on: b.status === '在途' || b.status === '托管中' }">{{ b.status }}</span></td>
            <td>{{ b.note || '—' }}</td>
            <td>
              <button class="btn ghost" :disabled="b.status !== '在途' || busy === b.id" @click="act(b.id, 'settle')">结清</button>
              <button class="btn ghost" :disabled="b.status !== '在途' || busy === b.id" @click="act(b.id, 'freeze')">冻结</button>
              <button class="btn ghost" :disabled="b.status !== '已冻结' || busy === b.id" @click="act(b.id, 'unfreeze')">解冻</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
