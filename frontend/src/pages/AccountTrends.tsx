import { TrendingUp } from 'lucide-react'
import { PageHeader } from '../components'
import StyleSection from './Style'

/** 画像（2026-11-18 用户裁决：tab 名由「经营」改「画像」，里面没有经营数据；2026-11-17 裁决：数据部分迁「数据」菜单——账号数据卡→数据·账号趋势，
视频表现周报→数据·作品表现；本页只留风格画像与自账号管理）。 */
export default function AccountTrends({ onNavigate }: { onNavigate?: (k: string) => void }) {
  return (
    <div>
      <PageHeader icon={TrendingUp} title="画像" desc="风格画像与自账号管理（cookie/同步）；数据看板在「数据」菜单" />
      <StyleSection onNavigate={onNavigate} />
    </div>
  )
}
