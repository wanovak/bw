import React, { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api'
import { AuthPage } from './AuthPage'
import { Ask, History, Account } from './parent'
import { Queue, Log, Knowledge, Dashboard, Team, Audit } from './operator'
import './styles.css'

function ParentShell({ user, onLogout }) {
  const [unread, setUnread] = useState(0)
  const loc = useLocation()
  useEffect(() => {
    api.get('/parent/notifications').then((r) => setUnread(r.unread)).catch(() => {})
  }, [loc.pathname])
  return (
    <div className="shell parent">
      <header className="topbar">
        <div className="brand"><img src="/icon.svg" alt="" width="28" height="28" /><span>Little Acorns</span></div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Ask />} />
          <Route path="/history" element={<History onRead={() => api.get('/parent/notifications').then((r) => setUnread(r.unread))} />} />
          <Route path="/account" element={<Account user={user} onLogout={onLogout} />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
      <nav className="bottomnav" aria-label="Main">
        <NavLink to="/" end>Ask</NavLink>
        <NavLink to="/history">History{unread > 0 && <span className="badge" aria-label={`${unread} updated`}>{unread}</span>}</NavLink>
        <NavLink to="/account">Account</NavLink>
      </nav>
    </div>
  )
}

function OperatorShell({ user, setUser, onLogout }) {
  const [summary, setSummary] = useState({ open_escalations: 0, high_priority: 0 })
  const loc = useLocation()
  const refresh = () => api.get('/operator/summary').then(setSummary).catch(() => {})
  useEffect(() => { refresh() }, [loc.pathname])
  return (
    <div className="shell operator">
      <header className="topbar">
        <div className="brand"><img src="/icon.svg" alt="" width="28" height="28" /><span>Little Acorns · Staff</span></div>
        <button className="link" onClick={onLogout}>Sign out</button>
      </header>
      <nav className="topnav" aria-label="Staff">
        <NavLink to="/" end>Questions{summary.open_escalations > 0 && <span className={'badge' + (summary.high_priority ? ' hot' : '')}>{summary.open_escalations}</span>}</NavLink>
        <NavLink to="/log">Q&amp;A log</NavLink>
        <NavLink to="/knowledge">Knowledge</NavLink>
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/team">Team</NavLink>
        <NavLink to="/audit">Audit</NavLink>
      </nav>
      <main className="content wide">
        {user.must_change_password && <PasswordBanner onDone={() => setUser({ ...user, must_change_password: false })} />}
        <Routes>
          <Route path="/" element={<Queue onChange={refresh} />} />
          <Route path="/log" element={<Log />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/team" element={<Team me={user} />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  )
}

function PasswordBanner({ onDone }) {
  const [open, setOpen] = useState(false)
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState('')
  async function save(e) {
    e.preventDefault()
    try {
      await api.post('/auth/change-password', { current_password: cur, new_password: next })
      onDone()
    } catch (err) {
      setMsg(err.message)
    }
  }
  return (
    <div className="alert warn">
      <strong>Please change your password.</strong> You're using a temporary password.{' '}
      {!open && <button className="link" onClick={() => setOpen(true)}>Change it now</button>}
      {open && (
        <form className="inline-form" onSubmit={save}>
          <input type="password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" />
          <input type="password" placeholder="New password (8+ characters)" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} autoComplete="new-password" />
          <button className="primary">Save</button>
          {msg && <span className="error-text">{msg}</span>}
        </form>
      )}
    </div>
  )
}

export default function App() {
  const [user, setUser] = useState(undefined)
  useEffect(() => {
    api.get('/auth/me').then(setUser).catch(() => setUser(null))
  }, [])
  async function logout() {
    await api.post('/auth/logout').catch(() => {})
    setUser(null)
  }
  if (user === undefined) return <div className="loading">Loading…</div>
  return (
    <BrowserRouter>
      {!user ? (
        <AuthPage onAuthed={setUser} />
      ) : user.role === 'operator' ? (
        <OperatorShell user={user} setUser={setUser} onLogout={logout} />
      ) : (
        <ParentShell user={user} onLogout={logout} />
      )}
    </BrowserRouter>
  )
}
