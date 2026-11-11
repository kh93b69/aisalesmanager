'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { authFetch as baseAuthFetch, refreshTokens, logoutAndRedirect } from '../components/api';

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

// Запрос с токеном. Если токен истёк (401) — один раз обновляем его и повторяем запрос,
// чтобы админку не выкидывало на вход каждый час (как в основной панели)
async function authFetch(url: string, options: RequestInit = {}) {
  let res = await baseAuthFetch(url, options);
  if (res.status === 401) {
    const refreshed = await refreshTokens();
    if (refreshed === "expired") {
      logoutAndRedirect(true);
      return res;
    }
    if (refreshed === "unavailable") {
      // Нет связи — не выходим из аккаунта, просто вернём ошибку
      return res;
    }
    res = await baseAuthFetch(url, options);
  }
  return res;
}

// Срок подписки для таблицы: дата или «бессрочно», истёкшая — с пометкой
function formatExpires(sub: Subscription): string {
  if (!sub.expires_at) return 'бессрочно';
  const date = new Date(sub.expires_at);
  if (isNaN(date.getTime())) return sub.expires_at;
  const text = date.toLocaleDateString('ru-RU');
  return date.getTime() < Date.now() ? `${text} (истекла)` : text;
}

export default function AdminPage() {
  const router = useRouter();
  const [users, setUsers] = useState<UserWithSub[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  // Текст ошибки последнего действия (загрузка, сохранение, удаление)
  const [error, setError] = useState('');
  const [editingUser, setEditingUser] = useState<string | null>(null);
  const [editSub, setEditSub] = useState<Subscription>({
    plan: 'free', status: 'active', max_bots: 1, max_dialogs_per_month: 500, expires_at: null
  });

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    if (!token) { router.push('/login'); return; }

    // Роль проверяет сервер (403 → на главную); localStorage может быть испорчен
    let parsed: { role?: string } | null = null;
    try {
      parsed = JSON.parse(localStorage.getItem('user') || 'null');
    } catch {
      parsed = null;
    }
    if (parsed && parsed.role && parsed.role !== 'admin') { router.push('/'); return; }

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
      if (!usersRes.ok || !statsRes.ok) {
        setError('Не удалось загрузить данные — обновите страницу');
      } else {
        const usersData = await usersRes.json();
        const statsData = await statsRes.json();
        setUsers(usersData.users || []);
        setStats(statsData);
        setError('');
      }
    } catch {
      setError('Нет связи с сервером');
    }
    setLoading(false);
  };

  // Текст ошибки из ответа сервера ({"detail": "..."})
  const readError = async (res: Response, fallback: string) => {
    try {
      const data = await res.json();
      if (data && typeof data.detail === 'string') return data.detail;
    } catch {
      // ответ не JSON
    }
    return fallback;
  };

  // Выполнить действие админки и показать ошибку, если не получилось
  const runAction = async (url: string, options: RequestInit, fallback: string) => {
    try {
      const res = await authFetch(url, options);
      if (!res.ok) {
        setError(await readError(res, fallback));
        return false;
      }
      setError('');
      return true;
    } catch {
      setError('Нет связи с сервером');
      return false;
    }
  };

  const saveSub = async (userId: string) => {
    const ok = await runAction(`${API_URL}/api/admin/users/${userId}/subscription`, {
      method: 'PUT',
      body: JSON.stringify(editSub),
    }, 'Не удалось сохранить подписку');
    if (ok) setEditingUser(null);
    loadData();
  };

  const deleteUser = async (userId: string) => {
    if (!confirm('Удалить пользователя и все его данные?')) return;
    await runAction(`${API_URL}/api/admin/users/${userId}`, { method: 'DELETE' }, 'Не удалось удалить пользователя');
    loadData();
  };

  const toggleAdmin = async (userId: string, currentRole: string) => {
    const newRole = currentRole === 'admin' ? 'user' : 'admin';
    await runAction(`${API_URL}/api/admin/users/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role: newRole }),
    }, 'Не удалось изменить роль');
    loadData();
  };

  if (loading) return <div className="empty-state">Загрузка...</div>;

  return (
    <div style={{ padding: 32, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 32 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700 }}>Админ-панель</h1>
        <button className="btn btn-outline" onClick={() => router.push('/')}>
          Назад
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

      {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* Таблица пользователей */}
      <h2 style={{ marginBottom: 16, fontSize: 18, fontWeight: 600 }}>Пользователи</h2>
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
            <th>Подписка до</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td style={{ color: 'var(--text)' }}>{u.email}</td>
              <td>
                <span className={`badge ${u.role === 'admin' ? 'badge-purple' : 'badge-green'}`}>
                  {u.role}
                </span>
              </td>
              <td>{u.subscription ? u.subscription.plan : 'нет подписки'}</td>
              <td>{u.bots_count}</td>
              <td>{u.dialogs_count}</td>
              <td>{u.subscription?.max_bots || 1}</td>
              {/* Без подписки бот отвечает в пределах 100 диалогов в месяц, тест бота недоступен */}
              <td>{u.subscription ? u.subscription.max_dialogs_per_month : 100}</td>
              <td>{u.subscription ? formatExpires(u.subscription) : '—'}</td>
              <td>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    className="btn btn-sm btn-outline"
                    onClick={() => {
                      setEditingUser(u.id);
                      setEditSub(u.subscription || {
                        plan: 'free', status: 'active', max_bots: 1, max_dialogs_per_month: 500, expires_at: null
                      });
                    }}
                  >
                    Подписка
                  </button>
                  <button className="btn btn-sm btn-outline" onClick={() => toggleAdmin(u.id, u.role)}>
                    {u.role === 'admin' ? 'User' : 'Admin'}
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
            <h3>Редактировать подписку</h3>

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

            <div className="form-group">
              <label>Действует до (пусто — бессрочно)</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  type="date"
                  value={editSub.expires_at ? editSub.expires_at.slice(0, 10) : ''}
                  onChange={(e) => setEditSub({ ...editSub, expires_at: e.target.value || null })}
                />
                <button className="btn btn-outline" type="button" onClick={() => setEditSub({ ...editSub, expires_at: null })}>
                  Бессрочно
                </button>
              </div>
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
