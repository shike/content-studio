import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { api } from '../api'

/** 首登强制改密屏：一次性密码登录后全屏拦截，改完才放行（R11.2）。 */
export default function ForceChangePassword({ username, onDone }: {
  username: string
  onDone: () => void
}) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (newPwd.length < 8) { setErr('新密码至少 8 位'); return }
    if (newPwd !== confirm) { setErr('两次输入的新密码不一致'); return }
    setBusy(true); setErr('')
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      })
      onDone()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2.5 mb-6">
          <img src="/icon.svg" alt="" className="size-10 rounded-xl" />
          <div>
            <div className="text-lg font-semibold text-zinc-900">设置新密码</div>
            <div className="text-xs text-slate-400">{username} · 当前为一次性密码，必须修改后才能使用</div>
          </div>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); submit() }} className="card p-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">当前密码（一次性密码）</label>
            <input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
              className="input w-full" placeholder="刚才登录用的密码" autoFocus />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">新密码（至少 8 位）</label>
            <input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)}
              className="input w-full" placeholder="新密码" />
          </div>
          <div>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
              className="input w-full" placeholder="再输入一次新密码" />
          </div>
          {err && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
          <button type="submit" className="btn-accent w-full" disabled={busy || !oldPwd || newPwd.length < 8}>
            {busy ? '保存中…' : '保存并进入'}
          </button>
        </form>
      </div>
    </div>
  )
}
