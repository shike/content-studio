import { TrendingUp } from 'lucide-react'
import { PageHeader } from '../components'
import StyleSection from './Style'

/** 账号经营（2026-11-17 用户裁决：数据部分迁「数据」菜单——账号数据卡→数据·账号趋势，
视频表现周报→数据·作品表现；本页只留风格画像与自账号管理）。 */
export default function AccountTrends({ onNavigate }: { onNavigate?: (k: string) => void }) {
  return (
    <div>
      <PageHeader icon={TrendingUp} title="账号经营" desc="风格画像沉淀与自账号管理（cookie/同步）；数据看板在「数据」菜单" />
      <StyleSection onNavigate={onNavigate} />
    </div>
  )
}
