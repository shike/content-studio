import { useState } from 'react'
import { KeyRound, User } from 'lucide-react'
import { api } from '../api'

/* 登录页（品牌化，参考 drop-harness moxt 语言）：左品牌区（墨绿渐变 + 粒子星尘 +
   衬线宣言）+ 右登录卡。窄屏（<lg）退化为单栏只留登录卡。 */

export default function Login({ onLogin }: { onLogin: (username: string) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!username.trim() || !password) return
    setBusy(true)
    setError('')
    try {
      await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.trim(), password }) })
      onLogin(username)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* 左：品牌区（墨绿渐变 + 粒子星尘 + 衬线宣言；窄屏隐藏） */}
      <div
        className="relative hidden lg:flex flex-col justify-center overflow-hidden text-[#f2f4f1]"
        style={{
          flex: '0 0 46%',
          background: 'radial-gradient(900px 600px at 30% 20%, #12241b 0%, #0a120d 45%, #050806 100%)',
          padding: '0 7%',
        }}
      >
        <div
          className="absolute inset-0 opacity-90"
          style={{
            backgroundImage:
              'radial-gradient(1.5px 1.5px at 18% 28%, rgba(34,197,94,.5) 50%, transparent 51%),' +
              'radial-gradient(1px 1px at 72% 14%, rgba(232,201,127,.35) 50%, transparent 51%),' +
              'radial-gradient(1px 1px at 84% 62%, rgba(34,197,94,.35) 50%, transparent 51%),' +
              'radial-gradient(1.5px 1.5px at 40% 76%, rgba(232,201,127,.28) 50%, transparent 51%),' +
              'radial-gradient(1px 1px at 58% 44%, rgba(34,197,94,.3) 50%, transparent 51%),' +
              'radial-gradient(1px 1px at 12% 60%, rgba(34,197,94,.28) 50%, transparent 51%)',
          }}
        />
        <div className="relative">
          <div className="mb-7 flex items-center gap-3">
            <img src="/icon.svg" alt="" className="size-11 rounded-xl" />
            <div>
              <div className="num-serif text-[21px] font-semibold tracking-wide text-white">跃迁内容工作室</div>
              <div className="mt-0.5 text-[11.5px] tracking-[3px] text-[#8b948b]">内容生产流水线</div>
            </div>
          </div>
          <div className="mb-7 h-0.5 w-[54px] bg-linear-to-r from-[#22c55e] to-transparent" />
          <div className="num-serif max-w-[460px] text-[30px] font-semibold leading-[1.5] text-white">
            让一条选题，沿着流水线
            <br />
            <span className="text-[#22c55e]">走到可发布的成品</span>
          </div>
          <div className="mt-6 max-w-[430px] text-[13px] leading-[2] text-[#a8b3a9]">
            · 选题深研 → 三版口播 → 数字人成片 → 公众号长文，一条流水线跑完内容生产全程<br />
            · 同行监控与拆解持续进料：赛道里谁在跑、什么打法有效，台账说话<br />
            · 每一步产出可验收、发布永远人工把关——AI 干活，你把关
          </div>
        </div>
      </div>

      {/* 右：登录区 */}
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <img src="/icon.svg" alt="" className="size-10 rounded-xl" />
            <div>
              <div className="text-lg font-semibold text-zinc-900">跃迁内容工作室</div>
              <div className="text-xs text-slate-400">内容生产流水线</div>
            </div>
          </div>
          <div className="card p-7">
            <div className="num-serif mb-1 text-center text-[18px] font-semibold tracking-wide text-[#171929]">
              登录
            </div>
            <div className="mb-5 text-center text-xs text-[#6f7683]">内容生产流水线 · 管理端</div>
            <form onSubmit={(e) => { e.preventDefault(); submit() }} className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-[#6f7683]">用户名</label>
                <div className="relative">
                  <User size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a6aeb9]" />
                  <input value={username} onChange={(e) => setUsername(e.target.value)}
                    className="input pl-9 w-full" placeholder="用户名" autoFocus />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[#6f7683]">密码</label>
                <div className="relative">
                  <KeyRound size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#a6aeb9]" />
                  <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                    className="input pl-9 w-full" placeholder="密码" />
                </div>
              </div>
              {error && <div className="text-xs text-red-500">{error}</div>}
              <button type="submit" disabled={busy || !username || !password}
                className="btn-accent w-full justify-center disabled:opacity-40 disabled:shadow-none">
                {busy ? '登录中…' : '登录'}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  )
}
