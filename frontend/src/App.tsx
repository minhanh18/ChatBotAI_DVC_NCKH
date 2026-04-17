import React, { useEffect, useMemo, useState } from 'react';
import { BarChart3, FolderOpen, LogOut, Lock, MessageSquare } from 'lucide-react';
import { ChatWindow } from './components/ChatWindow';
import { DocumentsPanel } from './components/DocumentsPanel';
import { AdminDashboard } from './components/AdminDashboard';
import type { AdminAuth } from './api/client';
import { adminDashboard } from './api/client';

type Tab = 'chat' | 'documents' | 'admin';
type RouteMode = 'user' | 'admin';

const ADMIN_STORAGE_KEY = 'chatbot_admin_auth';
const BOT_AVATAR = '/static/assets/img/chatbot/icon_chatbot_circle_final.png';

function getCurrentPath() {
  return window.location.pathname || '/';
}

function isAdminRoute(path: string) {
  return /(^|\/)admin(\/|$)/.test(path);
}

function isEmbeddedMode() {
  return new URLSearchParams(window.location.search).get('embed') === '1';
}

function isHostFullscreenMessage(data: unknown): data is { type: 'chatbot_host_mode'; fullscreen: boolean } {
  return Boolean(
    data &&
      typeof data === 'object' &&
      (data as { type?: string }).type === 'chatbot_host_mode' &&
      typeof (data as { fullscreen?: unknown }).fullscreen === 'boolean',
  );
}

function AdminLogin({ onLogin }: { onLogin: (auth: AdminAuth) => void }) {
  const [user, setUser] = useState('admin');
  const [pass, setPass] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await adminDashboard(user, pass);
      onLogin({ user, pass });
    } catch {
      setError('Sai thông tin đăng nhập admin');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center bg-[#f6f0ec] px-4">
      <div className="w-full max-w-sm bg-white rounded-[28px] border border-[#e7d4ca] shadow-[0_18px_50px_rgba(118,76,56,0.10)] p-7">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-12 h-12 rounded-2xl border border-[#ead6cb] bg-[#fff8f4] flex items-center justify-center overflow-hidden shrink-0">
            <img src={BOT_AVATAR} alt="Trợ lý hỗ trợ công dân" className="w-9 h-9 object-cover" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-[#6b4637]">Đăng nhập quản trị</h1>
            <p className="text-sm text-[#9d7867]">Giám sát hội thoại và quản lý tài liệu</p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder="Tên đăng nhập"
            className="w-full border border-[#dcc3b7] rounded-2xl px-4 py-3 text-sm focus:outline-none focus:border-[#b97b61]"
          />
          <input
            type="password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            placeholder="Mật khẩu"
            className="w-full border border-[#dcc3b7] rounded-2xl px-4 py-3 text-sm focus:outline-none focus:border-[#b97b61]"
          />
          {error && <p className="text-sm text-red-500">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-2xl bg-[#a86a4f] hover:bg-[#945843] disabled:bg-[#d7b7a9] text-white py-3 text-sm font-medium transition-colors"
          >
            {loading ? 'Đang xác thực...' : 'Vào màn hình giám sát'}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const [path, setPath] = useState(getCurrentPath());
  const [tab, setTab] = useState<Tab>('admin');
  const [adminAuth, setAdminAuth] = useState<AdminAuth | null>(() => {
    try {
      const raw = sessionStorage.getItem(ADMIN_STORAGE_KEY);
      return raw ? (JSON.parse(raw) as AdminAuth) : null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    const onPopState = () => setPath(getCurrentPath());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => {
    if (isAdminRoute(path)) {
      setTab('admin');
    }
  }, [path]);

  useEffect(() => {
    if (adminAuth) {
      sessionStorage.setItem(ADMIN_STORAGE_KEY, JSON.stringify(adminAuth));
      if (isAdminRoute(path)) setTab('admin');
    } else {
      sessionStorage.removeItem(ADMIN_STORAGE_KEY);
    }
  }, [adminAuth, path]);

  const mode: RouteMode = useMemo(() => (isAdminRoute(path) ? 'admin' : 'user'), [path]);
  const urlEmbedded = useMemo(() => isEmbeddedMode(), []);
  const [hostFullscreen, setHostFullscreen] = useState(false);
  const embedded = urlEmbedded && !hostFullscreen;

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (isHostFullscreenMessage(event.data)) {
        setHostFullscreen(event.data.fullscreen);
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'admin', label: 'Giám sát', icon: <BarChart3 size={16} /> },
    { id: 'documents', label: 'Tài liệu', icon: <FolderOpen size={16} /> },
    { id: 'chat', label: 'Chat', icon: <MessageSquare size={16} /> },
  ];

  if (mode === 'user') {
    return (
      <div className="h-screen bg-[#f6f0ec]">
        <ChatWindow
          sessionScope="user"
          adminMode={false}
          hideHistory
          allowWebSearch={false}
          embedded={embedded}
        />
      </div>
    );
  }

  if (!adminAuth) {
    return <AdminLogin onLogin={setAdminAuth} />;
  }

  return (
    <div className="flex flex-col h-screen bg-[#f6f0ec]">
      <nav className="bg-[#fffdfa]/95 backdrop-blur border-b border-[#ead8cf] px-4 flex items-center gap-1 h-14 shadow-sm">
        <div className="flex items-center gap-2 mr-4 min-w-0">
          <img
            src={BOT_AVATAR}
            alt="Trợ lý hỗ trợ công dân"
            className="w-8 h-8 rounded-full border border-[#ead8cf] bg-white object-cover"
          />
          <span className="font-semibold text-[#6b4637] text-sm truncate">Quản trị trợ lý hỗ trợ công dân</span>
        </div>
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs transition-colors ${
              tab === t.id
                ? 'bg-[#f9eee8] text-[#8c533f] font-medium border border-[#e1c1b2]'
                : 'text-[#8f7467] hover:text-[#734232] hover:bg-[#fff7f2]'
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
        <div className="ml-auto">
          <button
            onClick={() => setAdminAuth(null)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs text-[#8f7467] hover:text-[#734232] hover:bg-[#fff7f2] transition-colors"
          >
            <LogOut size={15} />
            Đăng xuất admin
          </button>
        </div>
      </nav>

      <div className="flex-1 overflow-hidden">
        {tab === 'chat' && <ChatWindow sessionScope="admin" adminMode allowWebSearch={false} />}
        {tab === 'documents' && <DocumentsPanel auth={adminAuth} />}
        {tab === 'admin' && <AdminDashboard auth={adminAuth} />}
      </div>
    </div>
  );
}
