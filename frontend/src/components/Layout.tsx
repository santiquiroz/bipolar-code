import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Icon, type IconName } from '@/components/Icon'
import { useHealth } from '@/hooks/useHealth'

const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: '/',           label: 'Dashboard',  icon: 'dashboard' },
  { to: '/chat',       label: 'Chat',       icon: 'chat' },
  { to: '/providers',  label: 'Providers',  icon: 'providers' },
  { to: '/models',     label: 'Models',     icon: 'models' },
  { to: '/usage',      label: 'Usage',      icon: 'usage' },
  { to: '/agents',     label: 'Agentes',    icon: 'agents' },
  { to: '/settings',   label: 'Settings',   icon: 'settings' },
]

function Brand() {
  return (
    <div>
      <h1 className="text-lg font-bold tracking-tight">Bipolar Code</h1>
      <p className="text-xs text-brand-300 mt-0.5">Gateway y orquestador de agentes</p>
    </div>
  )
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Principal" className="flex-1 p-4 space-y-1">
      {NAV.map(n => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.to === '/'}
          onClick={onNavigate}
          className={({ isActive }) =>
            `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300
            ${isActive ? 'bg-brand-600 text-white' : 'text-brand-200 hover:bg-brand-800 hover:text-white'}`
          }
        >
          <Icon name={n.icon} className="shrink-0" />
          {n.label}
        </NavLink>
      ))}
    </nav>
  )
}

function VersionFooter() {
  const { data } = useHealth()
  if (!data?.version) return null
  return <div className="px-6 py-4 text-xs text-brand-400 border-t border-brand-800">v{data.version}</div>
}

export function Layout() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()

  useEffect(() => { setOpen(false) }, [pathname])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col md:flex-row">
      <a
        href="#contenido"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:text-brand-700 focus:ring-2 focus:ring-brand-300"
      >
        Saltar al contenido
      </a>

      <header className="md:hidden flex items-center justify-between bg-brand-900 text-white px-4 py-3">
        <span className="text-base font-bold tracking-tight">Bipolar Code</span>
        <button
          type="button"
          aria-label="Abrir menú"
          aria-expanded={open}
          onClick={() => setOpen(true)}
          className="rounded-lg p-1.5 hover:bg-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          <Icon name="menu" />
        </button>
      </header>

      <aside className="hidden md:flex w-56 shrink-0 bg-brand-900 text-white flex-col">
        <div className="px-6 py-5 border-b border-brand-700"><Brand /></div>
        <NavLinks />
        <VersionFooter />
      </aside>

      {open && (
        <div className="md:hidden fixed inset-0 z-40 flex">
          <div className="w-64 bg-brand-900 text-white flex flex-col">
            <div className="px-6 py-5 border-b border-brand-700 flex items-start justify-between gap-2">
              <Brand />
              <button
                type="button"
                aria-label="Cerrar menú"
                onClick={() => setOpen(false)}
                className="rounded-lg p-1.5 hover:bg-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
              >
                <Icon name="close" />
              </button>
            </div>
            <NavLinks onNavigate={() => setOpen(false)} />
            <VersionFooter />
          </div>
          <div data-testid="menu-backdrop" className="flex-1 bg-black/50" onClick={() => setOpen(false)} />
        </div>
      )}

      <main id="contenido" className="flex-1 min-w-0 p-4 md:p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
