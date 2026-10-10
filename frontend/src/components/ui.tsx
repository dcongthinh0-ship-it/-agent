import type { ReactNode } from 'react'

type IconName = 'grid' | 'book' | 'layers' | 'activity' | 'search' | 'chevron' | 'arrow' | 'close' | 'file' | 'shield' | 'clock' | 'info' | 'refresh'

const paths: Record<IconName, ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21V5.5Z" /><path d="M4 18.5A2.5 2.5 0 0 1 6.5 16H20" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>,
  activity: <><path d="M3 12h4l3-7 4 14 3-7h4" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></>,
  chevron: <><path d="m9 18 6-6-6-6" /></>,
  arrow: <><path d="m15 18-6-6 6-6" /></>,
  close: <><path d="M5 5 19 19M19 5 5 19" /></>,
  file: <><path d="M6 3h8l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" /><path d="M14 3v5h5M8 12h8M8 16h6" /></>,
  shield: <><path d="m12 2 8 4v6c0 5-3.2 8.4-8 10-4.8-1.6-8-5-8-10V6l8-4Z" /><path d="m9 12 2 2 4-4" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M5.5 9A7 7 0 0 1 18 7l2 5M4 12l2 5a7 7 0 0 0 12.5-2" /></>,
}

export function Icon({ name, size = 19 }: { name: IconName; size?: number }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'warning' | 'quiet' | 'teal' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function Failure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="state state-error" role="alert"><Icon name="info" size={25} /><strong>读取暂时失败</strong><p>{message}</p><button className="text-button" onClick={onRetry}><Icon name="refresh" size={16} /> 重新读取</button></div>
}
