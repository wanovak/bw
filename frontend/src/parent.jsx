import React, { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtTime } from './api'

const STATUS_LABEL = {
  answered: null,
  answered_by_staff: 'Answered by center staff',
  pending_staff: 'Waiting for center staff',
  refused: 'Outside what I can help with',
  dismissed: 'Closed by staff',
}

function Answer({ item, onFeedback }) {
  const a = item.answer
  const label = STATUS_LABEL[item.status]
  return (
    <div className="answer">
      {label && <span className={'pill ' + item.status}>{label}</span>}
      <p className="answer-text">{a.text}</p>
      {a.sources.length > 0 && <p className="small muted">Based on: {a.sources.map((s) => s.title).join(', ')}</p>}
      {item.correction && (
        <div className="correction">
          <span className="pill corrected">Updated by staff · {fmtTime(item.correction.at)}</span>
          <p className="answer-text">{item.correction.text}</p>
          <p className="small muted">This is the corrected answer. The original is shown above for reference.</p>
        </div>
      )}
      {item.status === 'answered' && onFeedback && (
        <div className="feedback" aria-label="Was this helpful?">
          <span className="small muted">Helpful?</span>
          <button className={a.feedback === 'up' ? 'chip on' : 'chip'} onClick={() => onFeedback('up')} aria-label="Yes, helpful">👍</button>
          <button className={a.feedback === 'down' ? 'chip on' : 'chip'} onClick={() => onFeedback('down')} aria-label="No, not helpful">👎</button>
        </div>
      )}
    </div>
  )
}

export function Ask() {
  const [cfg, setCfg] = useState(null)
  const [mode, setMode] = useState('text')
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const [exIdx, setExIdx] = useState(0)
  const [category, setCategory] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const resultRef = useRef(null)

  useEffect(() => {
    api.get('/parent/config').then((c) => {
      setCfg(c)
      setExIdx(Math.floor(Math.random() * c.examples.length))
    })
  }, [])

  async function submit(q, m = 'text', cat = null) {
    setError('')
    setBusy(true)
    setResult(null)
    try {
      const item = await api.post('/parent/questions', { text: q, mode: m, category: cat })
      setResult(item)
      setText('')
      setCategory(null)
      setExIdx((i) => (cfg ? (i + 1 + Math.floor(Math.random() * (cfg.examples.length - 1))) % cfg.examples.length : i)) // rotate the example
      setTimeout(() => resultRef.current && resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function feedback(v) {
    await api.post(`/parent/answers/${result.answer.id}/feedback`, { value: v }).catch(() => {})
    setResult({ ...result, answer: { ...result.answer, feedback: v } })
  }

  if (!cfg) return <div className="loading">Loading…</div>
  const guidedCat = cfg.guided.find((g) => g.category === category)

  return (
    <div className="stack">
      <section>
        <h1>Ask {cfg.center_name.replace(' Learning Center', '')} anything</h1>
        <p className="muted">Get quick answers about our hours, tuition, illness policy, meals and tours. Questions I can't answer are passed to our staff.</p>
      </section>

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === 'text'} className={mode === 'text' ? 'tab active' : 'tab'} onClick={() => setMode('text')}>Type a question</button>
        <button role="tab" aria-selected={mode === 'guided'} className={mode === 'guided' ? 'tab active' : 'tab'} onClick={() => setMode('guided')}>Guided</button>
      </div>

      {mode === 'text' ? (
        <form className="card stack" onSubmit={(e) => { e.preventDefault(); if (text.trim()) submit(text.trim()) }}>
          <label className="sr-only" htmlFor="q">Your question</label>
          <textarea
            id="q"
            rows={3}
            value={text}
            maxLength={cfg.max_chars}
            onChange={(e) => setText(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={focused ? '' : `e.g. ${cfg.examples[exIdx]}`}
          />
          <div className="row between">
            <span className="small muted">{text.length}/{cfg.max_chars}</span>
            <button className="primary" disabled={busy || !text.trim()}>{busy ? 'Finding your answer…' : 'Ask'}</button>
          </div>
          <p className="small muted">Tip: avoid sharing names or phone numbers, you don't need them to get an answer.</p>
        </form>
      ) : (
        <div className="card stack">
          {!guidedCat ? (
            <>
              <p className="muted">What do you need help with?</p>
              <div className="grid">
                {cfg.guided.map((g) => (
                  <button key={g.category} className="tile" onClick={() => setCategory(g.category)}>{g.category}</button>
                ))}
              </div>
            </>
          ) : (
            <>
              <button className="link left" onClick={() => setCategory(null)}>← All topics</button>
              <h2>{guidedCat.category}</h2>
              <div className="stack tight">
                {guidedCat.questions.map((q) => (
                  <button key={q} className="option" disabled={busy} onClick={() => submit(q, 'guided', guidedCat.category)}>{q}</button>
                ))}
              </div>
            </>
          )}
          {busy && <p className="muted">Finding your answer…</p>}
        </div>
      )}

      {error && <div className="alert error" role="alert">{error}</div>}

      {result && (
        <section className="card stack" ref={resultRef} aria-live="polite">
          <p className="asked">{result.question}</p>
          {result.pii_scrubbed && <p className="small muted">🔒 Personal details were removed from your question before it was processed.</p>}
          <Answer item={result} onFeedback={feedback} />
          {result.status === 'pending_staff' && <p className="small muted">You'll see the answer on your <Link to="/history">History</Link> page.</p>}
        </section>
      )}
    </div>
  )
}

export function History({ onRead }) {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')
  const load = () => api.get('/parent/questions').then(setItems).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  async function opened(item, isOpen) {
    if (isOpen && item.unread) {
      await api.post(`/parent/questions/${item.id}/read`).catch(() => {})
      setItems((cur) => cur.map((i) => (i.id === item.id ? { ...i, unread: false } : i)))
      onRead && onRead()
    }
  }
  async function feedback(item, v) {
    await api.post(`/parent/answers/${item.answer.id}/feedback`, { value: v }).catch(() => {})
    setItems((cur) => cur.map((i) => (i.id === item.id ? { ...i, answer: { ...i.answer, feedback: v } } : i)))
  }

  if (error) return <div className="alert error">{error}</div>
  if (!items) return <div className="loading">Loading…</div>
  const q = ''
  return (
    <div className="stack">
      <h1>Your questions</h1>
      {items.length === 0 && <p className="muted">You haven't asked anything yet. <Link to="/">Ask your first question</Link>.</p>}
      {items.map((it) => (
        <details key={it.id} className={'card item' + (it.unread ? ' unread' : '')} open={it.unread} onToggle={(e) => opened(it, e.target.open)}>
          <summary>
            <span className="asked">{it.question}</span>
            <span className="small muted">
              {fmtTime(it.created_at)}
              {it.unread && <span className="badge inline">Updated</span>}
              {STATUS_LABEL[it.status] && <span className={'pill ' + it.status}>{STATUS_LABEL[it.status]}</span>}
              {it.correction && <span className="pill corrected">Updated by staff</span>}
            </span>
          </summary>
          {it.pii_scrubbed && <p className="small muted">🔒 Personal details were removed before this question was processed.</p>}
          <Answer item={it} onFeedback={(v) => feedback(it, v)} />
        </details>
      ))}
    </div>
  )
}

export function Account({ user, onLogout }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState(null)
  const [del, setDel] = useState(false)
  const [delPw, setDelPw] = useState('')

  async function changePw(e) {
    e.preventDefault()
    try {
      await api.post('/auth/change-password', { current_password: cur, new_password: next })
      setMsg({ ok: true, text: 'Password updated.' })
      setCur(''); setNext('')
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    }
  }
  async function deleteAccount(e) {
    e.preventDefault()
    try {
      await api.del('/parent/account', { password: delPw })
      onLogout()
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    }
  }
  return (
    <div className="stack">
      <h1>Account</h1>
      <div className="card"><strong>{user.name}</strong><br /><span className="muted">{user.email}</span></div>
      <section className="card stack">
        <h2>How your information is used</h2>
        <ul className="small">
          <li>Your questions are saved so you can see them here.</li>
          <li>Staff see your question with personal details removed and an anonymous ID, never your name or email.</li>
          <li>Deleting your account removes your name, email and original questions.</li>
        </ul>
      </section>
      <form className="card stack" onSubmit={changePw}>
        <h2>Change password</h2>
        <input type="password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" />
        <input type="password" placeholder="New password (8+ characters)" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} autoComplete="new-password" />
        <button className="primary">Update password</button>
      </form>
      {msg && <div className={'alert ' + (msg.ok ? 'ok' : 'error')} role="status">{msg.text}</div>}
      <button onClick={onLogout}>Sign out</button>
      {!del ? (
        <button className="danger-link" onClick={() => setDel(true)}>Delete my account…</button>
      ) : (
        <form className="card stack" onSubmit={deleteAccount}>
          <p><strong>This permanently deletes your account and question history.</strong> Enter your password to confirm.</p>
          <input type="password" placeholder="Password" value={delPw} onChange={(e) => setDelPw(e.target.value)} required />
          <div className="row"><button type="button" onClick={() => setDel(false)}>Cancel</button><button className="danger">Delete account</button></div>
        </form>
      )}
    </div>
  )
}
