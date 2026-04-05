// components/Layout.jsx
import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  { to: '/',         icon: '🏠', label: '仪表板'   },
  { to: '/accounts', icon: '👥', label: '账户列表' },
  { to: '/jobs',     icon: '🚀', label: '注册任务' },
  { to: '/settings', icon: '⚙️',  label: '配置管理' },
  { to: '/proxies',  icon: '🌐', label: '代理池'   },
]

export function Layout() {
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-slate-900 text-white flex flex-col">
        <div className="px-4 py-5 border-b border-slate-700/60">
          <h1 className="text-base font-bold text-blue-400 leading-tight">ChatGPT Register</h1>
          <p className="text-xs text-slate-400 mt-0.5">自动注册管理面板</p>
        </div>

        <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
          {NAV.map(({ to, icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors select-none ${
                  isActive
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <span className="text-base">{icon}</span>
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-3 border-t border-slate-700/60">
          <p className="text-xs text-slate-500">WebUI v0.1 · GPT Register</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}

