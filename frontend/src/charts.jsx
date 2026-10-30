import React from 'react'
import { pct } from './api'

const W = 600
const PAD = { l: 34, r: 8, t: 10, b: 22 }

function scale(n, max, h) {
  return h - (max ? (n / max) * h : 0)
}

export function BarChart({ data, xKey, yKey, label, height = 150, highlightKey = 'spike' }) {
  const max = Math.max(1, ...data.map((d) => d[yKey] || 0))
  const iw = W - PAD.l - PAD.r
  const ih = height - PAD.t - PAD.b
  const bw = iw / Math.max(1, data.length)
  const ticks = [0, Math.ceil(max / 2), max]
  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="chart" role="img" aria-label={label}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t + scale(t, max, ih)} y2={PAD.t + scale(t, max, ih)} className="grid" />
          <text x={PAD.l - 6} y={PAD.t + scale(t, max, ih) + 4} textAnchor="end" className="axis">{t}</text>
        </g>
      ))}
      {data.map((d, i) => {
        const h = ih - scale(d[yKey] || 0, max, ih)
        return (
          <rect key={i} x={PAD.l + i * bw + 1} y={PAD.t + ih - h} width={Math.max(1, bw - 2)} height={h} className={d[highlightKey] ? 'bar spike' : 'bar'}>
            <title>{`${d[xKey]}: ${d[yKey]}${d[highlightKey] ? ' (unusual spike)' : ''}`}</title>
          </rect>
        )
      })}
      <text x={PAD.l} y={height - 5} className="axis">{String(data[0]?.[xKey] || '').slice(5)}</text>
      <text x={W - PAD.r} y={height - 5} textAnchor="end" className="axis">{String(data[data.length - 1]?.[xKey] || '').slice(5)}</text>
    </svg>
  )
}

export function LineChart({ data, xKey, yKey, label, height = 150, percent = true, threshold }) {
  const iw = W - PAD.l - PAD.r
  const ih = height - PAD.t - PAD.b
  const n = Math.max(1, data.length - 1)
  const pts = data.map((d, i) => ({ x: PAD.l + (i / n) * iw, y: d[yKey] === null || d[yKey] === undefined ? null : PAD.t + scale(d[yKey], 1, ih), d }))
  const segments = []
  let cur = []
  pts.forEach((p) => {
    if (p.y === null) {
      if (cur.length) segments.push(cur)
      cur = []
    } else cur.push(p)
  })
  if (cur.length) segments.push(cur)
  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="chart" role="img" aria-label={label}>
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t + scale(t, 1, ih)} y2={PAD.t + scale(t, 1, ih)} className="grid" />
          <text x={PAD.l - 6} y={PAD.t + scale(t, 1, ih) + 4} textAnchor="end" className="axis">{percent ? pct(t) : t}</text>
        </g>
      ))}
      {threshold !== undefined && <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t + scale(threshold, 1, ih)} y2={PAD.t + scale(threshold, 1, ih)} className="threshold" />}
      {segments.map((s, i) => (
        <polyline key={i} points={s.map((p) => `${p.x},${p.y}`).join(' ')} className="line" />
      ))}
      {pts.filter((p) => p.y !== null).map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="2.5" className="dot"><title>{`${p.d[xKey]}: ${percent ? pct(p.d[yKey]) : p.d[yKey]}`}</title></circle>
      ))}
      <text x={PAD.l} y={height - 5} className="axis">{String(data[0]?.[xKey] || '').slice(5)}</text>
      <text x={W - PAD.r} y={height - 5} textAnchor="end" className="axis">{String(data[data.length - 1]?.[xKey] || '').slice(5)}</text>
    </svg>
  )
}
