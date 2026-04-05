import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout.jsx'
import { Dashboard } from './pages/Dashboard.jsx'
import { Accounts } from './pages/Accounts.jsx'
import { Jobs } from './pages/Jobs.jsx'
import { Settings } from './pages/Settings.jsx'
import { Proxies } from './pages/Proxies.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="settings" element={<Settings />} />
          <Route path="proxies" element={<Proxies />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

