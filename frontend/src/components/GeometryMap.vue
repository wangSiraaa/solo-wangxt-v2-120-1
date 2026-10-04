<template>
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <h2>台网几何与候选解对比</h2>
      <div class="muted">
        <span class="tag good">绿★ 真值</span>
        <span class="tag" :class="selectedRun ? 'warn' : ''">橙◆ 所选候选</span>
        <span class="tag raw">灰◇ 其他候选</span>
        <span class="tag good">▼ 纳入台站</span>
        <span class="tag excluded">▼ 本次排除</span>
        <span class="tag missing">▼ 缺测</span>
      </div>
    </div>
    <div class="muted" style="margin-top:-2px">
      台站状态按<span v-if="selectedRun">所选候选解 #{{ selectedRun.id }} 的输入快照</span>
      <span v-else>当前 {{ locatePhase }} 波定位选择</span>展示；
      排除/缺测台站不参与该次定位。
    </div>
    <div ref="mapEl" style="height: 420px"></div>
    <div v-if="selectedRun?.geometry" class="grid2" style="margin-top:6px">
      <dl class="kv">
        <dt>台站数</dt><dd>{{ selectedRun.geometry.n_stations }}</dd>
        <dt>最大方位空隙角</dt>
        <dd :style="{color: selectedRun.geometry.gap_deg > 180 ? 'var(--warn)' : 'var(--good)'}">
          {{ selectedRun.geometry.gap_deg }}°
          （{{ gapText[selectedRun.geometry.gap_verdict] }}）
        </dd>
      </dl>
      <dl class="kv">
        <dt>方向矩阵特征值比</dt>
        <dd :style="{color: selectedRun.geometry.eigenvalue_ratio > 1000 ? 'var(--bad)' : 'var(--good)'}">
          {{ selectedRun.geometry.eigenvalue_ratio }}
        </dd>
        <dt>近共线警告</dt>
        <dd>{{ selectedRun.geometry.collinear_warning ? '是 ⚠' : '否' }}</dd>
      </dl>
    </div>
    <div class="muted" style="margin-top:4px">
      候选解周围的椭圆为 1σ 水平误差椭圆；共线几何下它会沿无台站方向被显著拉长。
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import Plotly from 'plotly.js-dist-min'
import { kmToDeg } from '../lib/api.js'

const props = defineProps({
  stations: { type: Array, default: () => [] },
  scenario: Object,
  runs: { type: Array, default: () => [] },
  selectedRun: Object,
  picks: { type: Array, default: () => [] },
  locatePhase: { type: String, default: 'P' },
  excludedIds: { type: Array, default: () => [] },
})
const mapEl = ref(null)
const gapText = { good: '包围良好', one_sided: '单侧覆盖', poor: '严重单侧' }

onMounted(redraw)
watch(() => [props.stations, props.runs, props.selectedRun, props.picks,
             props.locatePhase, props.excludedIds], redraw, { deep: true })

// 每个台站在当前查看语境下的三态：included | excluded | missing | na
// 选中候选解时以该次运行的输入快照为准（历史可复盘）；否则按当前定位震相选择。
function stationStatusMap() {
  const map = new Map()
  if (props.selectedRun?.input_snapshot?.length) {
    for (const s of props.selectedRun.input_snapshot) {
      if (s.time_source === 'missing') map.set(s.station_code, 'missing')
      else if (s.excluded) map.set(s.station_code, 'excluded')
      else map.set(s.station_code, 'included')
    }
    return map
  }
  const ex = new Set(props.excludedIds)
  for (const p of props.picks) {
    if (p.phase !== props.locatePhase) continue
    if (p.effective_source === 'missing') map.set(p.station_code, 'missing')
    else if (ex.has(p.id)) map.set(p.station_code, 'excluded')
    else map.set(p.station_code, 'included')
  }
  return map
}

const STATUS_STYLE = {
  included: { color: '#f85149', label: '纳入' },
  excluded: { color: '#d29922', label: '本次排除' },
  missing: { color: '#6e7681', label: '缺测' },
  na: { color: '#8b949e', label: '非本次震相' },
}

function ellipseLonLat(run) {
  const u = run.uncertainty
  if (!u || !run.lon) return null
  const [a, b] = u.ellipse_semi_axes_km
  const az = (u.ellipse_major_axis_azimuth_deg ?? 0) * Math.PI / 180
  const lon = [], lat = []
  for (let i = 0; i <= 64; i++) {
    const th = (i / 64) * 2 * Math.PI
    const dx = a * Math.cos(th) * Math.cos(az) - b * Math.sin(th) * Math.sin(az)
    const dy = a * Math.cos(th) * Math.sin(az) + b * Math.sin(th) * Math.cos(az)
    const [dlo, dla] = kmToDeg(dx, dy, run.lat)
    lon.push(run.lon + dlo)
    lat.push(run.lat + dla)
  }
  return { lon, lat }
}

function redraw() {
  if (!mapEl.value) return
  const traces = []

  // 台站（按纳入 / 排除 / 缺测三态分开成轨迹，图例可区分）
  const statusMap = stationStatusMap()
  for (const key of ['included', 'excluded', 'missing', 'na']) {
    const group = props.stations.filter(s =>
      (statusMap.get(s.code) || 'na') === key)
    if (!group.length) continue
    const st = STATUS_STYLE[key]
    traces.push({
      x: group.map(s => s.lon),
      y: group.map(s => s.lat),
      text: group.map(s => `${s.code}（${st.label}${
        props.selectedRun ? ` · 候选#${props.selectedRun.id}` : ` · ${props.locatePhase}`}）`),
      type: 'scatter', mode: 'markers+text',
      marker: {
        symbol: 'triangle-down', size: key === 'excluded' ? 13 : 11,
        color: st.color,
        opacity: key === 'missing' ? 0.55 : 1,
        line: key === 'excluded' ? { color: '#e3b341', width: 1.5 } :
              { color: '#0d1117', width: 1 },
      },
      textposition: 'top center', textfont: { size: 10, color: st.color },
      name: `台站·${st.label}`,
      hovertemplate: '%{text}<br>(%{x:.4f}, %{y:.4f})<extra></extra>',
    })
  }

  // 真值
  if (props.scenario) {
    traces.push({
      x: [props.scenario.true_lon], y: [props.scenario.true_lat],
      type: 'scatter', mode: 'markers',
      marker: { symbol: 'star', size: 17, color: '#3fb950', line: { color: '#0d1117', width: 1 } },
      name: '已知真值',
      hovertemplate: `真值 ${props.scenario.true_lon.toFixed(4)}, ${props.scenario.true_lat.toFixed(4)}` +
        `<br>深度 ${props.scenario.true_depth_km} km<extra></extra>`,
    })
  }

  // 候选解（其他）。选中项是详情对象、与列表摘要身份不同，按 id 去重
  const others = props.runs.filter(
    r => r.locatable && r.id !== props.selectedRun?.id)
  if (others.length) {
    traces.push({
      x: others.map(r => r.lon), y: others.map(r => r.lat),
      text: others.map(r => `#${r.id} ${r.label}`),
      type: 'scatter', mode: 'markers',
      marker: { symbol: 'diamond', size: 10, color: '#8b949e' },
      name: '其他候选解', hovertemplate: '%{text}<extra></extra>',
    })
  }

  // 其他候选的误差椭圆（淡灰）
  for (const r of others) {
    const e = ellipseLonLat(r)
    if (e) traces.push({
      x: e.lon, y: e.lat, type: 'scatter', mode: 'lines',
      line: { color: 'rgba(139,148,158,.45)', width: 1 },
      showlegend: false, hoverinfo: 'skip',
    })
  }

  // 所选候选 + 椭圆
  if (props.selectedRun?.locatable) {
    const r = props.selectedRun
    const e = ellipseLonLat(r)
    if (e) traces.push({
      x: e.lon, y: e.lat, type: 'scatter', mode: 'lines',
      line: { color: '#d29922', width: 2 }, fill: 'toself',
      fillcolor: 'rgba(210,153,34,.14)',
      name: '所选解 1σ 椭圆', hoverinfo: 'skip',
    })
    traces.push({
      x: [r.lon], y: [r.lat],
      type: 'scatter', mode: 'markers',
      marker: { symbol: 'diamond', size: 14, color: '#e3b341',
                line: { color: '#0d1117', width: 1 } },
      name: '所选候选解',
      hovertemplate: `#${r.id} ${r.label}<br>(%{x:.4f}, %{y:.4f})<extra></extra>`,
    })
  }

  // 等比例经纬度网格（纬度方向乘 cos(lat) 修正）
  const allX = traces.flatMap(t => t.x || [])
  const allY = traces.flatMap(t => t.y || [])
  let xrange = null, yrange = null
  if (allX.length) {
    const x0 = Math.min(...allX), x1 = Math.max(...allX)
    const y0 = Math.min(...allY), y1 = Math.max(...allY)
    const mx = Math.max((x1 - x0) / 2 + 0.02, 0.04)
    const my = Math.max((y1 - y0) / 2 + 0.02, 0.04)
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
    const lat0 = cy * Math.PI / 180
    // 让 1° lon 与 1° lat 在屏幕上等长
    const mxAdj = Math.max(mx, my / Math.cos(lat0))
    const myAdj = mxAdj * Math.cos(lat0)
    xrange = [cx - mxAdj, cx + mxAdj]
    yrange = [cy - myAdj, cy + myAdj]
  }

  Plotly.react(mapEl.value, traces, {
    margin: { l: 52, r: 12, t: 12, b: 40 },
    paper_bgcolor: '#181f2e', plot_bgcolor: '#10151f',
    font: { color: '#c9d4e0', size: 11 },
    xaxis: { title: { text: '经度 °E' }, gridcolor: '#232d3d', range: xrange, scaleanchor: false },
    yaxis: { title: { text: '纬度 °N' }, gridcolor: '#232d3d', range: yrange },
    legend: { orientation: 'h', y: -0.18 },
    hovermode: 'closest',
  }, { displaylogo: false, scrollZoom: true })
}
</script>
