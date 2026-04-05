// pages/Proxies.jsx
import { useState, useEffect } from 'react'
import api from '../lib/api.js'
import { Badge, Spinner } from '../components/Badge.jsx'

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function Proxies() {
  const [proxies, setProxies] = useState([])
  const [input,   setInput]   = useState('')
  const [adding,  setAdding]  = useState(false)
  const [addErr,  setAddErr]  = useState('')
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    api.getProxies().then(setProxies).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const addProxy = async () => {
    const addr = input.trim()
    if (!addr) return
    setAdding(true); setAddErr('')
    try {
      await api.addProxy(addr)
      setInput('')
      load()
    } catch (e) {
      setAddErr(e.message)
    } finally {
      setAdding(false)
    }
  }

  const deleteProxy = async (addr) => {
    await api.deleteProxy(addr).catch(() => {})
    load()
  }

  const active   = proxies.filter(p => p.is_active).length
  const inactive = proxies.length - active

  return (
    <div className="p-6 space-y-5">
      <div>
        <h2 className="text-xl font-bold text-gray-800">代理池</h2>
        <p className="text-sm text-gray-500 mt-0.5">
          共 {proxies.length} 个代理 · 活跃 {active} · 禁用 {inactive}
        </p>
      </div>

      {/* Add proxy */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h3 className="font-semibold text-gray-700 mb-3">添加代理</h3>
        <div className="flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addProxy()}
            placeholder="http://user:pass@host:port"
            className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
          <button
            onClick={addProxy}
            disabled={adding || !input.trim()}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            {adding ? <Spinner /> : null}
            {adding ? '添加中…' : '添加'}
          </button>
        </div>
        {addErr && <p className="text-xs text-red-500 mt-2">{addErr}</p>}
        <p className="text-xs text-gray-400 mt-2">
          支持格式：<code className="bg-gray-100 px-1 rounded">http://host:port</code>、
          <code className="bg-gray-100 px-1 rounded">http://user:pass@host:port</code>、
          <code className="bg-gray-100 px-1 rounded">socks5://host:port</code>
        </p>
      </div>

      {/* Proxy table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-x-auto">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="font-semibold text-gray-700">代理列表</h3>
          <button onClick={load} className="text-xs text-blue-500 hover:underline">刷新</button>
        </div>
        {loading && proxies.length === 0 ? (
          <div className="text-center py-12 text-gray-400 flex justify-center"><Spinner /></div>
        ) : proxies.length === 0 ? (
          <p className="text-center py-12 text-gray-400 text-sm">暂无代理，请先添加</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                {['地址', '失败次数', '最后使用', '状态', '操作'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {proxies.map(p => (
                <tr key={p.address} className={`hover:bg-gray-50 transition-colors ${!p.is_active ? 'opacity-50' : ''}`}>
                  <td className="px-4 py-3 font-mono text-xs text-gray-700">{p.address}</td>
                  <td className="px-4 py-3 text-xs">
                    <span className={p.fail_count >= 3 ? 'text-red-500 font-medium' : 'text-gray-500'}>
                      {p.fail_count}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">{fmt(p.last_used)}</td>
                  <td className="px-4 py-3">
                    {p.is_active
                      ? <Badge color="green">活跃</Badge>
                      : <Badge color="red">禁用</Badge>}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => deleteProxy(p.address)}
                      className="text-xs text-red-400 hover:text-red-600 transition-colors"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

