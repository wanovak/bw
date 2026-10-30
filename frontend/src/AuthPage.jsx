import React, { useState } from 'react'
import { api } from './api'

export function AuthPage({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ name: '', email: '', password: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const user =
        mode === 'login'
          ? await api.post('/auth/login', { email: form.email, password: form.password })
          : await api.post('/auth/signup', form)
      onAuthed(user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth">
      <div className="brand big">
        <img src="/icon.svg" alt="" width="48" height="48" />
        <h1>Little Acorns</h1>
        <p className="muted">Quick answers about our center: hours, tuition, illness, meals and tours.</p>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'tab active' : 'tab'} onClick={() => setMode('login')}>Sign in</button>
        <button role="tab" aria-selected={mode === 'signup'} className={mode === 'signup' ? 'tab active' : 'tab'} onClick={() => setMode('signup')}>Create parent account</button>
      </div>
      <form className="card stack" onSubmit={submit}>
        {mode === 'signup' && (
          <label>Your name
            <input value={form.name} onChange={set('name')} required maxLength={80} autoComplete="name" />
          </label>
        )}
        <label>Email
          <input type="email" value={form.email} onChange={set('email')} required autoComplete="email" />
        </label>
        <label>Password
          <input type="password" value={form.password} onChange={set('password')} required minLength={mode === 'signup' ? 8 : undefined} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
        </label>
        {mode === 'signup' && (
          <p className="small muted">
            <strong>Privacy:</strong> We save your questions so you can see them again. Center staff only see your question with personal details (names, phone numbers, addresses) removed, plus an anonymous ID, never your name or email. Please don't include private details you don't need to.
          </p>
        )}
        {error && <div className="alert error" role="alert">{error}</div>}
        <button className="primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
      </form>
    </main>
  )
}
