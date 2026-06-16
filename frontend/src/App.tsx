import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { LayoutDashboard, Trophy, Swords, Users, Play, FileText } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Leaderboard from './pages/Leaderboard';
import Matches from './pages/Matches';
import Skills from './pages/Skills';
import Arena from './pages/Arena';
import Reports from './pages/Reports';

const navItems = [
  { to: '/', label: '仪表盘', icon: LayoutDashboard },
  { to: '/leaderboard', label: '排行榜', icon: Trophy },
  { to: '/matches', label: '比赛历史', icon: Swords },
  { to: '/skills', label: '技能管理', icon: Users },
  { to: '/arena', label: '竞技控制台', icon: Play },
  { to: '/reports', label: '报告', icon: FileText },
];

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex">
        <nav className="w-56 bg-white border-r border-gray-200 flex flex-col">
          <div className="px-5 py-5 border-b border-gray-100">
            <h1 className="text-lg font-bold text-gray-900">
              Skill 竞技场
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">Elo 排名系统</p>
          </div>
          <div className="flex-1 py-3">
            {navItems.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-5 py-2.5 text-sm transition-colors ${
                    isActive
                      ? 'text-blue-600 bg-blue-50 font-medium'
                      : 'text-gray-600 hover:text-gray-900 hover:bg-gray-50'
                  }`
                }
              >
                <Icon className="w-4 h-4" />
                {label}
              </NavLink>
            ))}
          </div>
          <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
            v0.3.0
          </div>
        </nav>
        <main className="flex-1 p-6 max-w-[1200px]">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/leaderboard" element={<Leaderboard />} />
            <Route path="/matches" element={<Matches />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/arena" element={<Arena />} />
            <Route path="/reports" element={<Reports />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
