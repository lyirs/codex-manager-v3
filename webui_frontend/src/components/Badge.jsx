// components/Badge.jsx
export function Badge({ color = 'gray', children }) {
  const colors = {
    green:  'bg-green-100 text-green-800',
    red:    'bg-red-100 text-red-800',
    yellow: 'bg-yellow-100 text-yellow-800',
    blue:   'bg-blue-100 text-blue-800',
    gray:   'bg-gray-100 text-gray-600',
    purple: 'bg-purple-100 text-purple-800',
    orange: 'bg-orange-100 text-orange-800',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colors[color] ?? colors.gray}`}>
      {children}
    </span>
  )
}

const STATUS_MAP = {
  '注册完成':               { color: 'green',  label: '✓ 注册完成' },
  failed:                  { color: 'red',    label: '✗ 失败' },
  running:                 { color: 'blue',   label: '⟳ 运行中' },
  done:                    { color: 'green',  label: '✓ 完成' },
  error:                   { color: 'red',    label: '✗ 错误' },
  email_creation_failed:   { color: 'orange', label: '✗ 邮件失败' },
  imported:                { color: 'purple', label: '导入' },
}

export function StatusBadge({ status }) {
  const { color = 'gray', label = status } = STATUS_MAP[status] ?? {}
  return <Badge color={color}>{label}</Badge>
}

export function Spinner({ size = 'sm' }) {
  const sz = size === 'sm' ? 'h-4 w-4' : 'h-6 w-6'
  return (
    <svg className={`animate-spin ${sz} text-blue-500`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  )
}

