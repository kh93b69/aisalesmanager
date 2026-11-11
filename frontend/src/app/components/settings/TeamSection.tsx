"use client";

// Настройки → Команда: сотрудники, которые видят диалоги (по своему городу или все).

import { useEffect, useState } from "react";
import { apiDelete, apiPost, apiPut, apiRequest, Bot, City, errorText, TeamMember } from "../api";

type Props = {
  bot: Bot;
  cities: City[];
};

export default function TeamSection({ bot, cities }: Props) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [cityId, setCityId] = useState("");
  const [telegramId, setTelegramId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Редактирование сотрудника
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editCityId, setEditCityId] = useState("");
  const [editTelegramId, setEditTelegramId] = useState("");

  const loadTeam = () => {
    setLoading(true);
    apiRequest<{ members: TeamMember[] }>(`/api/bots/${bot.id}/team`)
      .then((data) => setMembers(data.members || []))
      .catch((err) => setError(errorText(err)))
      .finally(() => setLoading(false));
  };

  // Первая загрузка. cancelled — чтобы ответ для старого бота не попал к новому
  useEffect(() => {
    let cancelled = false;
    setError("");
    setEditingId(null);
    setMembers([]);
    setLoading(true);
    apiRequest<{ members: TeamMember[] }>(`/api/bots/${bot.id}/team`)
      .then((data) => {
        if (!cancelled) setMembers(data.members || []);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bot.id]);

  const cityName = (id: string | null) => {
    if (!id) return "Все города";
    return cities.find((c) => c.id === id)?.name || "Город удалён";
  };

  const addMember = async () => {
    if (!email.trim()) {
      setError("Введите email сотрудника");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPost(`/api/bots/${bot.id}/team`, {
        email: email.trim().toLowerCase(),
        city_id: cityId || null,
        telegram_chat_id: telegramId.trim() || null,
      });
      setEmail("");
      setCityId("");
      setTelegramId("");
      loadTeam();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const startEdit = (m: TeamMember) => {
    setEditingId(m.id);
    setEditCityId(m.city_id || "");
    setEditTelegramId(m.telegram_chat_id || "");
  };

  const saveEdit = async () => {
    if (!editingId) return;
    setBusy(true);
    setError("");
    try {
      await apiPut(`/api/team/${editingId}`, {
        city_id: editCityId || null,
        telegram_chat_id: editTelegramId.trim() || null,
      });
      setEditingId(null);
      loadTeam();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const removeMember = async (m: TeamMember) => {
    if (!confirm(`Убрать ${m.email} из команды? Сотрудник потеряет доступ к диалогам.`)) return;
    setBusy(true);
    setError("");
    try {
      await apiDelete(`/api/team/${m.id}`);
      setMembers((prev) => prev.filter((x) => x.id !== m.id));
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  return (
    <div className="settings-section">
      <h3>Команда</h3>
      <p>
        Менеджеры видят диалоги (если указан город — только этого города), отмечают «Счёт отправлен» и «Оплачено».
        Настройки им недоступны. Сотрудник должен сначала зарегистрироваться в панели со своим email.
      </p>

      {loading && members.length === 0 ? (
        <div className="text-muted"><span className="spinner" /> Загружаем команду…</div>
      ) : members.length === 0 ? (
        <div className="empty-box">В команде пока никого нет</div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Город</th>
                <th>Telegram chat_id</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) =>
                editingId === m.id ? (
                  <tr key={m.id}>
                    <td>{m.email}</td>
                    <td>
                      <select className="input input-sm" value={editCityId} onChange={(e) => setEditCityId(e.target.value)}>
                        <option value="">Все города</option>
                        {cities.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="input input-sm"
                        value={editTelegramId}
                        onChange={(e) => setEditTelegramId(e.target.value)}
                        placeholder="123456789"
                      />
                    </td>
                    <td className="row-actions">
                      <button className="btn btn-sm btn-primary" onClick={saveEdit} disabled={busy}>Сохранить</button>
                      <button className="btn btn-sm btn-outline" onClick={() => setEditingId(null)} disabled={busy}>Отмена</button>
                    </td>
                  </tr>
                ) : (
                  <tr key={m.id}>
                    <td>{m.email}</td>
                    <td>{cityName(m.city_id)}</td>
                    <td>{m.telegram_chat_id ? <code>{m.telegram_chat_id}</code> : <span className="text-muted">—</span>}</td>
                    <td className="row-actions">
                      <button className="btn btn-sm btn-outline" onClick={() => startEdit(m)} disabled={busy}>Изменить</button>
                      <button className="btn btn-sm btn-ghost-danger" onClick={() => removeMember(m)} disabled={busy}>Убрать</button>
                    </td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="add-member">
        <div className="add-member-title">Добавить сотрудника</div>
        <div className="add-member-row">
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="email сотрудника"
          />
          <select className="input" value={cityId} onChange={(e) => setCityId(e.target.value)}>
            <option value="">Все города</option>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input
            className="input"
            value={telegramId}
            onChange={(e) => setTelegramId(e.target.value)}
            placeholder="Telegram chat_id (необязательно)"
          />
          <button className="btn btn-primary" onClick={addMember} disabled={busy}>
            {busy ? "…" : "Добавить"}
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  );
}
