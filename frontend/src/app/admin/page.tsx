'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL || '';

type Subscription = {
  plan: string;
  status: string;
  max_bots: number;
  max_dialogs_per_month: number;
  expires_at: string | null;
};

type UserWithSub = {
  id: string;
  email: string;
  role: string;
  created_at: string;
  subscription: Subscription | null;
  bots_count: number;
  dialogs_count: number;
};

type Stats = {
  users_count: number;
  bots_count: number;
  dialogs_count: number;
  messages_count: number;
};

function authFetch(url: string, options: RequestInit = {}) {
  const token = localStorage.getItem('access_token');
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }
  return fetch(url, { ...options, headers });
}

export default function AdminPage() {
  const router = useRouter();
  const [users, setUsers] = useState<UserWithSub[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingUser, setEditingUser] = useState<string | null>(null);
  const [editSub, setEditSub] = useState<Subscription>({
    plan: 'free', status: 'active', max_bots: 1, max_dialogs_per_month: 100, expires_at: null
  });

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    if (!token) { router.push('/login'); return; }

    // Проверяем что пользователь — админ
    const user = localStorage.getItem('user');
    if (user) {
      const parsed = JSON.parse(user);
      if (parsed.role !== 'admin') { router.push('/'); return; }
    }

    loadData();
  }, [router]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [usersRes, statsRes] = await Promise.all([
        authFetch(`${API_URL}/api/admin/users`),
        authFetch(`${API_URL}/api/admin/stats`),
      ]);

      if (usersRes.status === 403) { router.push('/'); return; }

      const usersData = await usersRes.json();
      const statsData = await statsRes.json();
      setUsers(usersData.users || []);
      setStats(statsData);
    } catch (err) {
      console.error('Ошибка загрузки:', err);
    }
    setLoading(false);
  };

  const saveSub = async (userId: string) => {
    await authFetch(`${API_URL}/api/admin/users/${userId}/subscription`, {
      method: 'PUT',
      body: JSON.stringify(editSub),
    });
    setEditingUser(null);
    loadData();
  };

  const deleteUser = async (userId: string) => {
    if (!confirm('Удалить пользователя и все его данные?')) return;
    await authFetch(`${API_URL}/api/admin/users/${userId}`, { method: 'DELETE' });
    loadData();
  };

  const toggleAdmin = async (userId: string, currentRole: string) => {
    const newRole = currentRole === 'admin' ? 'user' : 'admin';
    await authFetch(`${API_URL}/api/admin/users/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role: newRole }),
    });
    loadData();
  };

  if (loading) return <div className="empty-state">Загрузка...</div>;

  return (
    <div style={{ padding: 32, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <h1>Админ-панель</h1>
        <button className="btn btn-outline" onClick={() => router.push('/')}>
          Назад к дашборду
        </button>
      </div>

      {/* Статистика */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 32 }}>
          <div className="stat-card">
            <div className="stat-value">{stats.users_count}</div>
            <div className="stat-label">Пользователи</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats.bots_count}</div>
            <div className="stat-label">Боты</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats.dialogs_count}</div>
            <div className="stat-label">Диалоги</div>
          </div>
          <div className="stat-card">
            <div className="stat-value">{stats.messages_count}</div>
            <div className="stat-label">Сообщения</div>
          </div>
        </div>
      )}

      {/* Таблица пользователей */}
      <h2 style={{ marginBottom: 16 }}>Пользователи</h2>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Email</th>
            <th>Роль</th>
            <th>План</th>
            <th>Боты</th>
            <th>Диалоги</th>
            <th>Лимит ботов</th>
            <th>Лимит диалогов</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td>
              <td>
                <span className={`badge ${u.role === 'admin' ? 'badge-purple' : 'badge-green'}`}>
                  {u.role}
                </span>
              </td>
              <td>{u.subscription?.plan || 'free'}</td>
              <td>{u.bots_count}</td>
              <td>{u.dialogs_count}</td>
              <td>{u.subscription?.max_bots || 1}</td>
              <td>{u.subscription?.max_dialogs_per_month || 100}</td>
              <td>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    className="btn btn-sm btn-outline"
                    onClick={() => {
                      setEditingUser(u.id);
                      setEditSub(u.subscription || {
                        plan: 'free', status: 'active', max_bots: 1, max_dialogs_per_month: 100, expires_at: null
                      });
                    }}
                  >
                    Подписка
                  </button>
                  <button className="btn btn-sm btn-outline" onClick={() => toggleAdmin(u.id, u.role)}>
                    {u.role === 'admin' ? 'Убрать админ' : 'Сделать админ'}
                  </button>
                  <button className="btn btn-sm btn-danger" onClick={() => deleteUser(u.id)}>
                    Удалить
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Модалка редактирования подписки */}
      {editingUser && (
        <div className="modal-overlay" onClick={() => setEditingUser(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginBottom: 16 }}>Редактировать подписку</h3>

            <div className="form-group">
              <label>План</label>
              <select value={editSub.plan} onChange={(e) => setEditSub({ ...editSub, plan: e.target.value })}>
                <option value="free">Free</option>
                <option value="starter">Starter</option>
                <option value="pro">Pro</option>
                <option value="enterprise">Enterprise</option>
              </select>
            </div>

            <div className="form-group">
              <label>Статус</label>
              <select value={editSub.status} onChange={(e) => setEditSub({ ...editSub, status: e.target.value })}>
                <option value="active">Активна</option>
                <option value="expired">Истекла</option>
                <option value="cancelled">Отменена</option>
              </select>
            </div>

            <div className="form-group">
              <label>Макс. ботов</label>
              <input
                type="number"
                value={editSub.max_bots}
                onChange={(e) => setEditSub({ ...editSub, max_bots: parseInt(e.target.value) || 1 })}
              />
            </div>

            <div className="form-group">
              <label>Макс. диалогов/мес</label>
              <input
                type="number"
                value={editSub.max_dialogs_per_month}
                onChange={(e) => setEditSub({ ...editSub, max_dialogs_per_month: parseInt(e.target.value) || 100 })}
              />
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={() => saveSub(editingUser)}>Сохранить</button>
              <button className="btn btn-outline" onClick={() => setEditingUser(null)}>Отмена</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
