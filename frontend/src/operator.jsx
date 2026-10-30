import React, { useCallback, useEffect, useState } from 'react'
import { api, ApiError, fmtTime, pct, qs } from './api'
import { BarChart, LineChart } from './charts'

const FLAG_LABEL = {
  pii_scrubbed: 'PII scrubbed',
  sensitive: 'Sensitive',
  emergency: 'Emergency',
  off_topic: 'Off-topic',
  injection_attempt: 'Injection attempt',
  escalated: 'Escalated',
  llm_error: 'LLM error',
}
const REASON_LABEL = {
  no_answer: 'No answer in knowledge base',
  low_confidence: 'Low confidence',
  sensitive: 'Sensitive topic',
  llm_error: 'AI unavailable',
}

function Flags({ flags }) {
  return flags.filter((f) => FLAG_LABEL[f]).map((f) => (
    <span key={f} className={'pill flag-' + f}>{FLAG_LABEL[f]}</span>
  ))
}

function Conf({ value }) {
  if (value === null || value === undefined) return <span className="muted">no score</span>
  const cls = value >= 0.9 ? 'good' : value >= 0.7 ? 'mid' : 'low'
  return <span className={'conf ' + cls}>{pct(value)}</span>
}

/** Submits an operator action, handling the non-blocking PII warning (409 pii_warning). */
async function withPiiConfirm(send) {
  try {
    return await send(false)
  } catch (e) {
    if (e instanceof ApiError && e.detail && e.detail.code === 'pii_warning') {
      const ok = window.confirm(`This text appears to contain personal information (${e.detail.types.join(', ')}). Publish it anyway?`)
      if (!ok) return null
      return await send(true)
    }
    throw e
  }
}

// ---------------- Questions queue ----------------

function EscalationCard({ item, onDone }) {
  const [answer, setAnswer] = useState('')
  const sensitive = item.flags.includes('sensitive')
  const [addKb, setAddKb] = useState(!sensitive)
  const [addEntry, setAddEntry] = useState(!sensitive)
  const [dismissing, setDismissing] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const r = await withPiiConfirm((confirm_pii) =>
        api.post(`/operator/escalations/${item.escalation_id}/answer`, { answer, add_to_kb: addKb, add_as_entry: addEntry, confirm_pii })
      )
      if (r) onDone()
    } catch (err) {
      setError(err.message)
      if (err.status === 409) setTimeout(onDone, 1500)
    } finally {
      setBusy(false)
    }
  }
  async function dismiss(e) {
    e.preventDefault()
    try {
      await api.post(`/operator/escalations/${item.escalation_id}/dismiss`, { reason })
      onDone()
    } catch (err) {
      setError(err.message)
    }
  }

  const open = item.escalation_status === 'open'
  return (
    <article className={'card stack' + (item.priority === 'high' && open ? ' high' : '')}>
      <div className="row wrap between">
        <span className="small muted">{item.parent} · {fmtTime(item.created_at)}</span>
        <span>
          {item.priority === 'high' && <span className="pill flag-sensitive">High priority</span>}
          <span className="pill">{REASON_LABEL[item.reason] || item.reason}</span>
        </span>
      </div>
      <p className="asked">{item.question}</p>
      <div><Flags flags={item.flags} /></div>
      {item.flags.includes('pii_scrubbed') && <p className="small muted">🔒 PII was scrubbed from this question.</p>}
      {open && item.draft && (
        <div className="draft">
          <p className="small muted">AI draft (not shown to the parent) · confidence <Conf value={item.confidence} /></p>
          <p>{item.draft}</p>
          <button type="button" className="link left" onClick={() => setAnswer(item.draft)}>Use as starting point</button>
        </div>
      )}
      {open ? (
        dismissing ? (
          <form className="stack" onSubmit={dismiss}>
            <input placeholder="Reason (e.g. spam, duplicate)" value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={300} />
            <div className="row"><button type="button" onClick={() => setDismissing(false)}>Cancel</button><button className="danger">Dismiss</button></div>
          </form>
        ) : (
          <form className="stack" onSubmit={submit}>
            <textarea rows={3} placeholder="Type your answer to the parent…" value={answer} onChange={(e) => setAnswer(e.target.value)} required maxLength={2000} />
            <label className="check"><input type="checkbox" checked={addKb} onChange={(e) => setAddKb(e.target.checked)} /> Use this answer for future questions</label>
            <label className="check"><input type="checkbox" checked={addEntry} onChange={(e) => setAddEntry(e.target.checked)} /> Also add as a knowledge entry</label>
            {sensitive && <p className="small muted">Sensitive question: both boxes default to off so a personal reply isn't reused for other families.</p>}
            <div className="row">
              <button type="button" onClick={() => setDismissing(true)}>Dismiss…</button>
              <button className="primary" disabled={busy}>{busy ? 'Sending…' : 'Send answer'}</button>
            </div>
          </form>
        )
      ) : (
        <div className="small">
          {item.escalation_status === 'answered'
            ? <><strong>Answered by {item.answered_by}</strong> · {fmtTime(item.resolved_at)}<p>{item.staff_answer}</p></>
            : <><strong>Dismissed by {item.answered_by}</strong>: {item.dismiss_reason}</>}
        </div>
      )}
      {error && <div className="alert error" role="alert">{error}</div>}
    </article>
  )
}

export function Queue({ onChange }) {
  const [status, setStatus] = useState('open')
  const [items, setItems] = useState(null)
  const load = useCallback(() => api.get(`/operator/escalations?status=${status}`).then(setItems), [status])
  useEffect(() => { setItems(null); load() }, [load])
  return (
    <div className="stack">
      <div className="row wrap between">
        <h1>Questions needing staff</h1>
        <div className="tabs compact">
          {['open', 'answered', 'dismissed'].map((s) => (
            <button key={s} className={status === s ? 'tab active' : 'tab'} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>
          ))}
        </div>
      </div>
      {!items ? <div className="loading">Loading…</div> : items.length === 0 ? <p className="muted">{status === 'open' ? 'All caught up. No questions are waiting.' : 'Nothing here yet.'}</p> : items.map((it) => (
        <EscalationCard key={it.escalation_id} item={it} onDone={() => { load(); onChange && onChange() }} />
      ))}
    </div>
  )
}

// ---------------- Q&A log ----------------

function LogRow({ item, onChanged }) {
  const [correcting, setCorrecting] = useState(false)
  const [text, setText] = useState(item.corrected_text || item.answer)
  const [error, setError] = useState('')
  const canCorrect = ['answered', 'answered_by_staff'].includes(item.status)

  async function reviewed() {
    try { await api.post(`/operator/answers/${item.answer_id}/reviewed`); onChanged() } catch (e) { setError(e.message) }
  }
  async function submit(e) {
    e.preventDefault()
    try {
      const r = await withPiiConfirm((confirm_pii) => api.post(`/operator/answers/${item.answer_id}/correction`, { text, confirm_pii }))
      if (r) { setCorrecting(false); onChanged() }
    } catch (err) { setError(err.message) }
  }
  return (
    <article className="card stack">
      <div className="row wrap between">
        <span className="small muted">{item.parent} · {fmtTime(item.created_at)} · {item.category || 'Uncategorized'}</span>
        <span><Conf value={item.confidence} /> <span className={'pill st-' + item.status}>{item.status.replace(/_/g, ' ')}</span></span>
      </div>
      <p className="asked">{item.question}</p>
      <div><Flags flags={item.flags} />{item.feedback === 'down' && <span className="pill flag-sensitive">👎 parent</span>}{item.feedback === 'up' && <span className="pill">👍 parent</span>}</div>
      <p className="answer-text">{item.answer}{item.generated_by === 'operator' && <span className="small muted"> (written by staff)</span>}</p>
      {item.sources.length > 0 && <p className="small muted">Sources: {item.sources.map((s) => s.title).join(', ')}</p>}
      {item.corrected_text && <div className="correction"><span className="pill corrected">Corrected</span><p>{item.corrected_text}</p></div>}
      {correcting ? (
        <form className="stack" onSubmit={submit}>
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} required maxLength={2000} />
          <p className="small muted">The parent will see both the original and your correction, and the correction is used for future similar questions.</p>
          <div className="row"><button type="button" onClick={() => setCorrecting(false)}>Cancel</button><button className="primary">Submit correction</button></div>
        </form>
      ) : (
        <div className="row wrap">
          <span className="small muted">{item.review_status === 'unreviewed' ? 'Not reviewed' : item.review_status === 'reviewed' ? '✓ Reviewed' : '✓ Corrected'}</span>
          {item.review_status === 'unreviewed' && canCorrect && <button onClick={reviewed}>Mark correct</button>}
          {canCorrect && <button onClick={() => setCorrecting(true)}>{item.corrected_text ? 'Correct again' : 'Correct…'}</button>}
        </div>
      )}
      {error && <div className="alert error" role="alert">{error}</div>}
    </article>
  )
}

export function Log() {
  const [f, setF] = useState({ needs_review: false, max_confidence: '', status: '', flag: '', review_status: '', date_from: '', date_to: '' })
  const [data, setData] = useState(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const limit = 25
  const load = useCallback(() => {
    api.get('/operator/log' + qs({ ...f, limit, offset })).then(setData).catch((e) => setError(e.message))
  }, [f, offset])
  useEffect(() => { load() }, [load])
  const set = (k) => (e) => { setOffset(0); setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }) }
  return (
    <div className="stack">
      <h1>Q&amp;A log</h1>
      <div className="card filters">
        <label className="check"><input type="checkbox" checked={f.needs_review} onChange={set('needs_review')} /> Needs review</label>
        <label>Confidence below
          <select value={f.max_confidence} onChange={set('max_confidence')}>
            <option value="">Any</option><option value="0.5">50%</option><option value="0.7">70%</option><option value="0.9">90%</option><option value="0.95">95%</option>
          </select>
        </label>
        <label>Status
          <select value={f.status} onChange={set('status')}>
            <option value="">Any</option><option value="answered">Answered</option><option value="pending_staff">Waiting for staff</option><option value="answered_by_staff">Answered by staff</option><option value="refused">Refused</option><option value="dismissed">Dismissed</option>
          </select>
        </label>
        <label>Flag
          <select value={f.flag} onChange={set('flag')}>
            <option value="">Any</option>{Object.entries(FLAG_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Review
          <select value={f.review_status} onChange={set('review_status')}>
            <option value="">Any</option><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="corrected">Corrected</option>
          </select>
        </label>
        <label>From <input type="date" value={f.date_from} onChange={set('date_from')} /></label>
        <label>To <input type="date" value={f.date_to} onChange={set('date_to')} /></label>
      </div>
      {error && <div className="alert error">{error}</div>}
      {!data ? <div className="loading">Loading…</div> : (
        <>
          <p className="small muted">{data.total} result{data.total === 1 ? '' : 's'}</p>
          {data.items.map((it) => <LogRow key={it.answer_id} item={it} onChanged={load} />)}
          <div className="row between">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>← Newer</button>
            <button disabled={offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>Older →</button>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------- Knowledge base ----------------

function EntryForm({ entry, categories, onSaved, onCancel }) {
  const [f, setF] = useState(entry || { title: '', category: 'Other', body: '', active: true })
  const [error, setError] = useState('')
  const [versions, setVersions] = useState(null)
  async function save(e) {
    e.preventDefault()
    try {
      if (entry && entry.id) await api.put(`/operator/knowledge/${entry.id}`, f)
      else await api.post('/operator/knowledge', f)
      onSaved()
    } catch (err) { setError(err.message) }
  }
  async function remove() {
    if (!window.confirm('Delete this entry? The assistant will stop using it immediately.')) return
    await api.del(`/operator/knowledge/${entry.id}`)
    onSaved()
  }
  return (
    <form className="card stack" onSubmit={save}>
      <label>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required maxLength={200} /></label>
      <label>Category
        <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{categories.map((c) => <option key={c}>{c}</option>)}</select>
      </label>
      <label>Content<textarea rows={8} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} required maxLength={8000} /></label>
      <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active (used to answer questions)</label>
      {error && <div className="alert error">{error}</div>}
      <div className="row wrap">
        <button type="button" onClick={onCancel}>Cancel</button>
        {entry && entry.id && <button type="button" className="danger" onClick={remove}>Delete</button>}
        {entry && entry.id && <button type="button" className="link" onClick={() => api.get(`/operator/knowledge/${entry.id}/versions`).then(setVersions)}>Version history</button>}
        <button className="primary">Save</button>
      </div>
      {versions && (versions.length === 0 ? <p className="small muted">No earlier versions.</p> : versions.map((v) => (
        <details key={v.id}><summary className="small">{fmtTime(v.changed_at)} · {v.changed_by}</summary><p className="small">{v.body}</p></details>
      )))}
    </form>
  )
}

function QaPairs() {
  const [pairs, setPairs] = useState(null)
  const [editing, setEditing] = useState(null)
  const load = () => api.get('/operator/qa-pairs').then(setPairs)
  useEffect(() => { load() }, [])
  async function save(e) {
    e.preventDefault()
    await api.put(`/operator/qa-pairs/${editing.id}`, { question: editing.question, answer: editing.answer, active: editing.active })
    setEditing(null); load()
  }
  if (!pairs) return <div className="loading">Loading…</div>
  return (
    <div className="stack">
      <p className="muted small">Answers written or corrected by staff. They take priority over the handbook when the assistant answers.</p>
      {pairs.length === 0 && <p className="muted">None yet.</p>}
      {pairs.map((p) => editing && editing.id === p.id ? (
        <form key={p.id} className="card stack" onSubmit={save}>
          <label>Question<input value={editing.question} onChange={(e) => setEditing({ ...editing, question: e.target.value })} required /></label>
          <label>Answer<textarea rows={4} value={editing.answer} onChange={(e) => setEditing({ ...editing, answer: e.target.value })} required /></label>
          <label className="check"><input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active</label>
          <div className="row"><button type="button" onClick={() => setEditing(null)}>Cancel</button><button className="primary">Save</button></div>
        </form>
      ) : (
        <article key={p.id} className={'card stack' + (p.active ? '' : ' inactive')}>
          <p className="asked">{p.question}</p>
          <p>{p.answer}</p>
          <div className="row wrap between">
            <span className="small muted">{p.source.replace('_', ' ')} · {p.created_by || 'seed'} · {fmtTime(p.created_at)}{!p.active && ' · inactive'}</span>
            <span className="row"><button onClick={() => setEditing(p)}>Edit</button>
              <button className="danger" onClick={async () => { if (window.confirm('Delete this approved answer?')) { await api.del(`/operator/qa-pairs/${p.id}`); load() } }}>Delete</button></span>
          </div>
        </article>
      ))}
    </div>
  )
}

export function Knowledge() {
  const [tab, setTab] = useState('entries')
  const [data, setData] = useState(null)
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState(null)
  const load = useCallback(() => api.get('/operator/knowledge' + qs({ q })).then(setData), [q])
  useEffect(() => { load() }, [load])
  return (
    <div className="stack">
      <div className="row wrap between">
        <h1>Knowledge</h1>
        <div className="tabs compact">
          <button className={tab === 'entries' ? 'tab active' : 'tab'} onClick={() => setTab('entries')}>Handbook entries</button>
          <button className={tab === 'qa' ? 'tab active' : 'tab'} onClick={() => setTab('qa')}>Staff-approved answers</button>
        </div>
      </div>
      {tab === 'qa' ? <QaPairs /> : !data ? <div className="loading">Loading…</div> : editing ? (
        <EntryForm entry={editing === 'new' ? null : editing} categories={data.categories} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
      ) : (
        <>
          <div className="row">
            <input type="search" placeholder="Search entries…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="primary" onClick={() => setEditing('new')}>+ New</button>
          </div>
          <p className="small muted">Changes apply to the very next question parents ask.</p>
          {data.entries.map((e) => (
            <article key={e.id} className={'card stack' + (e.active ? '' : ' inactive')}>
              <div className="row wrap between"><strong>{e.title}</strong><span className="pill">{e.category}</span></div>
              <p className="clamp">{e.body}</p>
              <div className="row wrap between">
                <span className="small muted">Updated {fmtTime(e.updated_at)} by {e.updated_by}{!e.active && ' · inactive'}</span>
                <button onClick={() => setEditing(e)}>Edit</button>
              </div>
            </article>
          ))}
        </>
      )}
    </div>
  )
}

// ---------------- Dashboard ----------------

function Stat({ label, value, sub }) {
  return <div className="stat"><div className="stat-value">{value}</div><div className="stat-label">{label}</div>{sub && <div className="small muted">{sub}</div>}</div>
}

export function Dashboard() {
  const [days, setDays] = useState(30)
  const [d, setD] = useState(null)
  useEffect(() => { setD(null); api.get(`/operator/dashboard?days=${days}`).then(setD) }, [days])
  if (!d) return <div className="loading">Loading…</div>
  const t = d.totals
  const spikes = d.series.filter((s) => s.spike)
  return (
    <div className="stack">
      <div className="row wrap between">
        <h1>Dashboard</h1>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time range">
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </select>
      </div>
      {t.asked === 0 && <p className="muted">No questions in this period yet.</p>}
      <div className="stats">
        <Stat label="Questions asked" value={t.asked} />
        <Stat label="Average confidence" value={pct(t.avg_confidence)} />
        <Stat label="Needed staff answer" value={pct(t.escalation_rate)} sub={`${t.escalated} questions`} />
        <Stat label="Avg. time to staff answer" value={t.avg_hours_to_staff_answer === null ? '–' : t.avg_hours_to_staff_answer + ' h'} />
      </div>
      <section className="card stack">
        <h2>Questions per day</h2>
        <BarChart data={d.series} xKey="day" yKey="asked" label="Questions per day" />
        {spikes.length > 0 ? <p className="alert warn small">Unusual spike{spikes.length > 1 ? 's' : ''}: {spikes.map((s) => `${s.day} (${s.asked})`).join(', ')}. Highlighted in orange.</p> : <p className="small muted">No unusual spikes in this period.</p>}
        <p className="small muted">{d.spike_rule}</p>
      </section>
      <section className="card stack">
        <h2>Staff-answer rate over time</h2>
        <p className="small muted">Share of each day's questions that the assistant couldn't answer and staff had to handle. Falling means the knowledge base is improving.</p>
        <LineChart data={d.series} xKey="day" yKey="escalation_rate" label="Escalation rate per day" />
      </section>
      <section className="card stack">
        <h2>Average confidence over time</h2>
        <LineChart data={d.series} xKey="day" yKey="avg_confidence" label="Average confidence per day" threshold={0.9} />
        <p className="small muted">Dashed line: the 90% auto-answer threshold.</p>
      </section>
      <section className="card stack">
        <h2>Last 48 hours</h2>
        <BarChart data={d.hourly} xKey="hour" yKey="asked" label="Questions per hour, last 48 hours" height={120} highlightKey="none" />
      </section>
      <div className="two-col">
        <section className="card stack">
          <h2>By topic</h2>
          {d.by_category.map((c) => <div key={c.category} className="row between"><span>{c.category}</span><strong>{c.count}</strong></div>)}
          <h2>Input type</h2>
          {d.by_mode.map((m) => <div key={m.mode} className="row between"><span>{m.mode === 'guided' ? 'Guided flow' : 'Typed'}</span><strong>{m.count}</strong></div>)}
        </section>
        <section className="card stack">
          <h2>Most asked</h2>
          {d.top_questions.map((q) => <div key={q.question} className="row between"><span>{q.question}</span><strong>{q.count}</strong></div>)}
        </section>
      </div>
      <section className="card stack">
        <h2>Safety &amp; quality</h2>
        <div className="stats small-stats">
          <Stat label="Off-topic / refused" value={t.off_topic} />
          <Stat label="Injection attempts" value={t.injection_attempts} />
          <Stat label="Sensitive flagged" value={t.sensitive} />
          <Stat label="AI errors" value={t.llm_errors} />
          <Stat label="PII scrubbed" value={t.pii_scrubbed} />
        </div>
      </section>
    </div>
  )
}

// ---------------- Team & audit ----------------

export function Team({ me }) {
  const [ops, setOps] = useState(null)
  const [f, setF] = useState({ name: '', email: '', password: '' })
  const [msg, setMsg] = useState(null)
  const load = () => api.get('/operator/operators').then(setOps)
  useEffect(() => { load() }, [])
  async function create(e) {
    e.preventDefault()
    try {
      await api.post('/operator/operators', f)
      setMsg({ ok: true, text: `Created ${f.email}. They'll be asked to change the temporary password at first sign-in.` })
      setF({ name: '', email: '', password: '' }); load()
    } catch (err) { setMsg({ ok: false, text: err.message }) }
  }
  async function toggle(o) {
    try { await api.post(`/operator/operators/${o.id}/${o.active ? 'deactivate' : 'activate'}`); load(); setMsg(null) } catch (err) { setMsg({ ok: false, text: err.message }) }
  }
  return (
    <div className="stack">
      <h1>Team</h1>
      <p className="muted small">All staff have the same access. Parent accounts are never shown here.</p>
      {ops && ops.map((o) => (
        <div key={o.id} className={'card row wrap between' + (o.active ? '' : ' inactive')}>
          <span><strong>{o.name}</strong>{o.id === me.id && ' (you)'}<br /><span className="small muted">{o.email}{!o.active && ' · deactivated'}</span></span>
          {o.id !== me.id && <button onClick={() => toggle(o)}>{o.active ? 'Deactivate' : 'Reactivate'}</button>}
        </div>
      ))}
      <form className="card stack" onSubmit={create}>
        <h2>Add a staff account</h2>
        <input placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
        <input type="email" placeholder="Email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
        <input type="text" placeholder="Temporary password (8+ characters)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required minLength={8} autoComplete="off" />
        <button className="primary">Create account</button>
      </form>
      {msg && <div className={'alert ' + (msg.ok ? 'ok' : 'error')} role="status">{msg.text}</div>}
    </div>
  )
}

export function Audit() {
  const [rows, setRows] = useState(null)
  useEffect(() => { api.get('/operator/audit?limit=200').then(setRows) }, [])
  if (!rows) return <div className="loading">Loading…</div>
  return (
    <div className="stack">
      <h1>Audit trail</h1>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.id}><td>{fmtTime(r.created_at)}</td><td>{r.actor}</td><td>{r.action}</td><td>{r.target}{r.detail ? ` · ${r.detail}` : ''}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  )
}
