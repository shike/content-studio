import { useState } from 'react'

/* 品牌与内容配置表单（共享组件）：管理后台（平台管理员管任意租户）与
   设置页（租户管理员自助）共用同一份字段与保存逻辑，避免两处漂移。 */

export interface BrandShape {
  label_line1: string
  label_line2: string
  signature: string
  persona: string
  accent: string
  primary: string
  asr_vocab: string
  cover_slogan: string
  audience_note: string
}

export function BrandForm({ initial, onSave }: {
  initial: Partial<BrandShape>
  onSave: (payload: BrandShape) => Promise<void>
}) {
  const [brand, setBrand] = useState<BrandShape>({
    label_line1: initial.label_line1 ?? '',
    label_line2: initial.label_line2 ?? '',
    signature: initial.signature ?? '',
    persona: initial.persona ?? '',
    accent: initial.accent ?? '',
    primary: initial.primary ?? '',
    asr_vocab: initial.asr_vocab ?? '',
    cover_slogan: initial.cover_slogan ?? '',
    audience_note: initial.audience_note ?? '',
  })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const save = async () => {
    setBusy(true); setMsg(''); setErr('')
    try {
      await onSave(brand)
      setMsg('已保存（新出片/新文章按此渲染）')
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="block space-y-1">
          <span className="text-xs text-slate-500">角标栏目 · 第一行</span>
          <input value={brand.label_line1} onChange={(e) => setBrand({ ...brand, label_line1: e.target.value })}
            className="input w-full" placeholder="如：跃迁（留空=平台默认）" />
        </label>
        <label className="block space-y-1">
          <span className="text-xs text-slate-500">角标栏目 · 第二行</span>
          <input value={brand.label_line2} onChange={(e) => setBrand({ ...brand, label_line2: e.target.value })}
            className="input w-full" placeholder="如：内容（留空=平台默认）" />
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-xs text-slate-500">头图署名（生成头图右下的账号名）</span>
        <input value={brand.signature} onChange={(e) => setBrand({ ...brand, signature: e.target.value })}
          className="input w-full max-w-md" placeholder="如：跃迁内容" />
      </label>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="block space-y-1">
          <span className="text-xs text-slate-500">点缀色（角标/暖黄框，hex）</span>
          <input value={brand.accent} onChange={(e) => setBrand({ ...brand, accent: e.target.value })}
            className="input w-full" placeholder="#F2C14E（留空=平台默认）" />
        </label>
        <label className="block space-y-1">
          <span className="text-xs text-slate-500">图表主色（hex）</span>
          <input value={brand.primary} onChange={(e) => setBrand({ ...brand, primary: e.target.value })}
            className="input w-full" placeholder="#16a34a（留空=平台默认）" />
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-xs text-slate-500">头图口号（渲染在公众号头图右下，留空=不渲染）</span>
        <input value={brand.cover_slogan} onChange={(e) => setBrand({ ...brand, cover_slogan: e.target.value })}
          className="input w-full max-w-md" placeholder="如：让交付可验收" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-slate-500">受众域口径（注入深研与拆解提示词：目标读者是谁、行业语境）</span>
        <textarea value={brand.audience_note} onChange={(e) => setBrand({ ...brand, audience_note: e.target.value })}
          className="h-20 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-[12px] leading-relaxed outline-none focus:border-slate-400 focus:bg-white"
          placeholder="如：面向制造业中小企业老板与 AI 从业者" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-slate-500">内容人设（注入全部内容生成的提示词：身份、商业模式、读者、差异化）</span>
        <textarea value={brand.persona} onChange={(e) => setBrand({ ...brand, persona: e.target.value })}
          className="h-32 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-[12px] leading-relaxed outline-none focus:border-slate-400 focus:bg-white"
          placeholder="描述该租户创作者的身份、业务模式、目标读者与差异化优势" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-slate-500">ASR 领域词表（转写纠偏的行业词句，留空=平台默认）</span>
        <textarea value={brand.asr_vocab} onChange={(e) => setBrand({ ...brand, asr_vocab: e.target.value })}
          className="h-20 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-[12px] leading-relaxed outline-none focus:border-slate-400 focus:bg-white"
          placeholder="以下是关于××行业的中文口播内容。" />
      </label>
      <button onClick={save} disabled={busy} className="btn-accent btn-xs disabled:opacity-40">
        {busy ? '保存中…' : '保存品牌配置'}
      </button>
      {msg && <div className="text-xs text-emerald-600">{msg}</div>}
      {err && <div className="text-xs text-red-500">{err}</div>}
    </div>
  )
}
