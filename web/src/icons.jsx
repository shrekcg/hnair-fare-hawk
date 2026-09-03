// 统一图标：基于开源 lucide-react（GitHub: lucide-icons/lucide，月下载量 3.9 亿+）
// 统一 size=14 / strokeWidth=2 的线性风格，替换原 @ant-design/icons 使用点。
import {
  Calendar, ChevronDown, ChevronLeft, ChevronRight, ClipboardList, Compass, FileText, HelpCircle, LayoutDashboard,
  MessageCircle, Pencil, Plus, Power, Rocket, Send, Settings, ShieldCheck, Trash2,
} from 'lucide-react'

const wrap = (C) => (props) => <C size={typeof props.size === 'number' ? props.size : 14} strokeWidth={2} {...props} />

export const CalendarIcon = wrap(Calendar)
export const ChevronDownIcon = wrap(ChevronDown)
export const ChevronLeftIcon = wrap(ChevronLeft)
export const ChevronRightIcon = wrap(ChevronRight)
export const TasksIcon = wrap(ClipboardList)          // 监控任务
export const CompassIcon = wrap(Compass)              // 航线查询
export const HistoryIcon = wrap(FileText)             // 历史日志
export const OverviewIcon = wrap(LayoutDashboard)     // 总览
export const NotifyIcon = wrap(MessageCircle)         // 通知管理
export const EditIcon = wrap(Pencil)
export const PlusIcon = wrap(Plus)
export const PowerIcon = wrap(Power)
export const BrandIcon = wrap(Rocket)                 // 顶栏品牌
export const SendIcon = wrap(Send)
export const SettingsIcon = wrap(Settings)
export const MonitorIcon = wrap(ShieldCheck)          // 监控管理
export const DeleteIcon = wrap(Trash2)
export const QuestionIcon = wrap(HelpCircle)          // 教学提示（卡片标题旁 ?）