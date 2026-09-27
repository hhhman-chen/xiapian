import { FormEvent, useState } from 'react'
import * as api from '../api'

interface LoginProps {
  onLoggedIn: (user: { username: string; name: string }) => void
}

export default function Login({ onLoggedIn }: LoginProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const u = await api.login(username.trim(), password)
      onLoggedIn(u)
    } catch {
      setError('账号或密码不对')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">虾片</div>
        <p className="login-sub">文字即时间线 · 试用</p>
        <label>
          账号
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          密码
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <p className="login-error">{error}</p>}
        <button type="submit" disabled={busy || !username || !password}>
          {busy ? '登录中…' : '进入'}
        </button>
      </form>
    </div>
  )
}
