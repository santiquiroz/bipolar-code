import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  { to: '/',       label: 'Dashboard', icon: '⬡' },
  { to: '/models', label: 'Models',    icon: '◈' },
  { to: '/usage',  label: 'Usage',     icon: '◎' },
]

export function Layout() {
  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Sidebar */}
      <aside className="w-56 bg-brand-900 text-white flex flex-col">
        <div className="px-6 py-5 border-b border-brand-700">
          <h1 className="text-lg font-bold tracking-tight">Bipolar Code</h1>
          <p className="text-xs text-brand-300 mt-0.5">LiteLLM Proxy Manager</p>
        </div>
        <nav className="flex-1 p-4 space-y-1">
          {NAV.map(n => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors
                ${isActive ? 'bg-brand-600 text-white' : 'text-brand-200 hover:bg-brand-800 hover:text-white'}`
              }
            >
              <span className="text-lg leading-none">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-6 py-4 text-xs text-brand-400 border-t border-brand-800">
          Proxy: localhost:4001
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
