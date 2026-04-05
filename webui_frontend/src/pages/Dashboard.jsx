// pages/Dashboard.jsx
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../lib/api.js'
import { StatusBadge, Spinner } from '../components/Badge.jsx'

function StatCard({ label, value, sub, color = 'blue', icon }) {
  const ring = {
    blue:  'border-l-blue-500',
    green: 'border-l-green-500',
    red:   'border-l-red-500',
    amber: 'border-l-amber-500',
  }[color] ?? 'border-l-blue-500'

  return (
    <div className={`bg-white rounded-xl shadow-sm border border-gray-100 border-l-4 ${ring} p-5`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider font-medium">{label}</p>
          <p className="text-3xl font-bold text-gray-800 mt-1">{value ?? '—'}</p>
          {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
        </div>
        <span className="text-3xl opacity-60">{icon}</span>
      </div>
    </div>
  )
}

export function Dashboard() {
  const [stats, setStats]   = useState(null)
  const [jobs, setJobs]     = useState([])
  const [proxies, setProxies] = useState([])
  const [form, setForm]     = useState({ count: 1, engine: 'camoufox', provider: 'imap:0' })
  const [starting, setStarting] = useState(false)
  const [msg, setMsg]       = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    const load = () => {
      api.getStats().then(setStats).catch(() => {})
      api.getJobs().then(setJobs).catch(() => {})
      api.getProxies().then(setProxies).catch(() => {})
    }
    load()
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [])

  const success  = stats?.['注册完成'] ?? 0
  const total    = stats?.total ?? 0
  const rate     = total ? Math.round((success / total) * 100) : 0
  const active   = jobs.filter(j => j.status === 'running').length
  const activeProxy = proxies.filter(p => p.is_active).length

  const startJob = async () => {
    setStarting(true)
    setMsg('')
    try {
      const { job_id } = await api.startJob(form)
      setMsg(`任务 ${job_id} 已启动`)
      setTimeout(() => navigate('/jobs'), 800)
    } catch (e) {
      setMsg(`错误：${e.message}`)
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h2 className="text-xl font-bold text-gray-800">仪表板</h2>
        <p className="text-sm text-gray-500 mt-0.5">系统概览 · 实时数据每 5 秒刷新</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard label="账户总数"   value={total}  icon="👤" color="blue" />
        <StatCard label="注册成功"   value={success} sub={`成功率 ${rate}%`} icon="✅" color="green" />
        <StatCard label="活跃任务"   value={active}  icon="🚀" color="amber" />
        <StatCard label="活跃代理"   value={activeProxy} icon="🌐" color="blue" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Quick start */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <h3 className="font-semibold text-gray-700 mb-4">快速启动注册</h3>
          <div className="space-y-3">
            <label className="block">
              <span className="text-xs text-gray-500 font-medium uppercase">注册数量</span>
              <input
                type="number" min={1} max={50}
                value={form.count}
                onChange={e => setForm(f => ({ ...f, count: +e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium uppercase">浏览器引擎</span>
              <select
                value={form.engine}
                onChange={e => setForm(f => ({ ...f, engine: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="camoufox">Camoufox (推荐)</option>
                <option value="playwright">Playwright</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-500 font-medium uppercase">邮件服务</span>
              <select
                value={form.provider}
                onChange={e => setForm(f => ({ ...f, provider: e.target.value }))}
                className="mt-1 block w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="imap:0">IMAP 账户 1</option>
                <option value="imap:1">IMAP 账户 2</option>
                <option value="gptmail">GptMail</option>
                <option value="npcmail">NpcMail</option>
                <option value="yydsmail">YYDSMail</option>
              </select>
            </label>
            {msg && <p className="text-xs text-blue-600">{msg}</p>}
            <button
              onClick={startJob}
              disabled={starting}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
            >
              {starting ? <Spinner /> : '🚀'}
              {starting ? '启动中…' : '开始注册'}
            </button>
          </div>
        </div>

        {/* Recent jobs */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-gray-700">最近任务</h3>
            <button onClick={() => navigate('/jobs')} className="text-xs text-blue-500 hover:underline">查看全部 →</button>
          </div>
          {jobs.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-6">暂无任务</p>
          ) : (
            <div className="space-y-2">
              {jobs.slice(0, 6).map(j => (
                <div key={j.id} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                  <div>
                    <span className="text-xs font-mono text-gray-600">{j.id}</span>
                    <span className="text-xs text-gray-400 ml-2">{j.provider} · ×{j.count}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-500">{j.success}/{j.done}</span>
                    <StatusBadge status={j.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

