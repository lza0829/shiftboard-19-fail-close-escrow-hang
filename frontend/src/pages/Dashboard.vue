<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const data = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    data.value = await getJson('/api/dashboard')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>总览</h1>
    <p class="lead">看清谁在值班、有多少在途批次、有没有交班卡在半路。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <template v-if="data">
      <div class="stats">
        <div class="stat"><div class="k">班次数</div><div class="v">{{ data.班次数 }}</div></div>
        <div class="stat"><div class="k">在途批次</div><div class="v">{{ data.在途批次数 }}</div></div>
        <div class="stat"><div class="k">进行中交班</div><div class="v">{{ data.进行中交班 }}</div></div>
        <div class="stat"><div class="k">托管批次</div><div class="v">{{ data.托管批次数 }}</div></div>
        <div class="stat"><div class="k">锁定班次</div><div class="v">{{ data.锁定班次数 }}</div></div>
      </div>
      <p class="msg" :class="{ ok: data.锁托管同源 }">
        {{ data.锁托管同源 ? '进行中数、班次锁与托管集合同源一致' : '警告：锁/托管与进行中交班不同源' }}
      </p>
      <div class="panel" v-if="data.最近闭环证明">
        <h2>最近闭环证明 · 交班单 #{{ data.最近闭环证明.handover_id }}（{{ data.最近闭环证明.status }}）</h2>
        <div class="stats">
          <div class="stat"><div class="k">锁已释放</div><div class="v">{{ data.最近闭环证明.锁已释放 ? '是' : '否' }}</div></div>
          <div class="stat"><div class="k">交班方班次态</div><div class="v">{{ data.最近闭环证明.交班方班次态 || '—' }}</div></div>
          <div class="stat"><div class="k">接班方班次态</div><div class="v">{{ data.最近闭环证明.接班方班次态 || '—' }}</div></div>
          <div class="stat"><div class="k">托管归位批次</div><div class="v">{{ data.最近闭环证明.托管归位批次数 }}</div></div>
          <div class="stat"><div class="k">托管已清空</div><div class="v">{{ data.最近闭环证明.托管已清空 ? '是' : '否' }}</div></div>
          <div class="stat"><div class="k">允许再次完成</div><div class="v">{{ data.最近闭环证明.允许再次完成 ? '是' : '否' }}</div></div>
        </div>
        <p class="lead" style="margin:0">
          关闭时间 {{ data.最近闭环证明.closed_at || '—' }}
          <template v-if="data.最近闭环证明.closure_reason"> · 关闭原因：{{ data.最近闭环证明.closure_reason }}</template>
        </p>
      </div>
      <div class="panel">
        <h2>班次状态</h2>
        <table>
          <thead><tr><th>班次</th><th>值班人</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="s in data.班次" :key="s.id">
              <td>{{ s.name }}</td>
              <td>{{ s.keeper }}</td>
              <td><span class="tag" :class="{ on: s.status === '值班中' }">{{ s.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
