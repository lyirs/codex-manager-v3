// pages/Accounts.jsx
import { useState, useEffect, useCallback } from 'react'
import api from '../lib/api.js'
import { StatusBadge } from '../components/Badge.jsx'

const STATUSES = ['', '注册完成', 'failed', 'email_creation_failed', 'imported']
const PAGE_SIZE = 50

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text ?? '').then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <button onClick={copy} title="复制" className="text-gray-400 hover:text-blue-500 transition-colors ml-1">
      {copied ? '✓' : '⎘'}
    </button>
  )
}

export function Accounts() {
  const [rows, setRows]     = useState([])
  const [total, setTotal]   = useState(0)
  const [status, setStatus] = useState('')
  const [page, setPage]     = useState(0)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getAccounts({ status, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then(d => { setRows(d.items); setTotal(d.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [status, page])

  useEffect(() => { load() }, [load])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-gray-800">账户列表</h2>
          <p className="text-sm text-gray-500 mt-0.5">共 {total} 条记录</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={e => { setStatus(e.target.value); setPage(0) }}
            className="border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          >
            {STATUSES.map(s => (
              <option key={s} value={s}>{s || '全部状态'}</option>
            ))}
          </select>
          <a
            href={api.exportUrl('csv')}
            className="bg-white border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            导出 CSV
          </a>
          <a
            href={api.exportUrl('json')}
            className="bg-white border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            导出 JSON
          </a>
          <button
            onClick={load}
            className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-3 py-2 text-sm font-medium transition-colors"
          >
            刷新
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              {['邮箱', '密码', '状态', '服务商', 'Access Token', '注册时间'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loading && rows.length === 0 && (
              <tr><td colSpan={6} className="text-center py-12 text-gray-400">加载中…</td></tr>
            )}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={6} className="text-center py-12 text-gray-400">暂无数据</td></tr>
            )}
            {rows.map(r => (
              <tr key={r.email} className="hover:bg-gray-50 transition-colors">
                <td className="px-4 py-3 font-mono text-xs text-gray-700 whitespace-nowrap">
                  {r.email}<CopyBtn text={r.email} />
                </td>
                <td className="px-4 py-3 font-mono text-xs text-gray-500 whitespace-nowrap">
                  <span className="select-all">{r.password}</span><CopyBtn text={r.password} />
                </td>
                <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                <td className="px-4 py-3 text-xs text-gray-500">{r.provider || '—'}</td>
                <td className="px-4 py-3 max-w-[180px]">
                  {r.access_token ? (
                    <div className="flex items-center gap-1">
                      <span className="font-mono text-xs text-gray-400 truncate">{r.access_token.slice(0, 20)}…</span>
                      <CopyBtn text={r.access_token} />
                    </div>
                  ) : <span className="text-gray-300 text-xs">—</span>}
                </td>
                <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                  {r.created_at?.slice(0, 19) || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">
            第 {page + 1} / {pages} 页 · 共 {total} 条
          </span>
          <div className="flex gap-1">
            <button
              disabled={page === 0}
              onClick={() => setPage(p => p - 1)}
              className="px-3 py-1 rounded border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
            >
              上一页
            </button>
            <button
              disabled={page >= pages - 1}
              onClick={() => setPage(p => p + 1)}
              className="px-3 py-1 rounded border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

