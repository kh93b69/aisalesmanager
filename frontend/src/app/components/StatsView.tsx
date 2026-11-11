"use client";

// Статистика по диалогам за период: общие цифры и таблица по городам.

import { useEffect, useState } from "react";
import { apiRequest, errorText, formatPercent, Stats } from "./api";

const PERIODS = [7, 30, 90];

type Props = {
  botId: string;
};

export default function StatsView({ botId }: Props) {
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    apiRequest<Stats>(`/api/bots/${botId}/stats?days=${days}`)
      .then((data) => {
        if (!cancelled) setStats(data);
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
  }, [botId, days]);

  const byStatus = stats?.by_status || {};
  const total = stats?.total || 0;
  const paid = byStatus.paid || 0;
  const cityRows = stats?.by_city || [];
  // Самый большой город — для ширины полосок
  let maxCityTotal = 1;
  for (const row of cityRows) {
    if (row.total > maxCityTotal) maxCityTotal = row.total;
  }

  const cards = [
    { label: "Всего обращений", value: total, cls: "" },
    { label: "🔥 Ждут счёт", value: byStatus.hot || 0, cls: "stat-orange" },
    { label: "Счёт отправлен", value: byStatus.invoice_sent || 0, cls: "stat-blue" },
    { label: "Оплачено", value: paid, cls: "stat-green" },
    { label: "Конверсия в оплату", value: formatPercent(paid, total), cls: "stat-green" },
  ];

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Статистика</h1>
          <p>По диалогам, начатым за выбранный период</p>
        </div>
        <div className="segmented">
          {PERIODS.map((p) => (
            <button key={p} className={days === p ? "active" : ""} onClick={() => setDays(p)}>
              {p} дней
            </button>
          ))}
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading && !stats && (
        <div className="list-placeholder"><span className="spinner" /> Считаем статистику…</div>
      )}

      {stats && (
        <div style={{ opacity: loading ? 0.6 : 1, transition: "opacity 0.2s" }}>
          <div className="stats-grid">
            {cards.map((c) => (
              <div key={c.label} className={`stat-card ${c.cls}`}>
                <div className="stat-value">{c.value}</div>
                <div className="stat-label">{c.label}</div>
              </div>
            ))}
          </div>

          <div className="card" style={{ marginTop: 20 }}>
            <h3 className="card-title">По городам</h3>
            {cityRows.length === 0 ? (
              <div className="list-placeholder">За этот период диалогов нет</div>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Город</th>
                      <th className="num">Всего</th>
                      <th className="num">Горячих</th>
                      <th className="num">Счёт отправлен</th>
                      <th className="num">Оплачено</th>
                      <th className="num">Потеряно</th>
                      <th className="num">Конверсия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cityRows.map((row) => (
                      <tr key={row.city_id || "none"}>
                        <td>
                          <div>{row.city_name || "Город не указан"}</div>
                          <div className="bar">
                            <div className="bar-fill" style={{ width: `${(row.total / maxCityTotal) * 100}%` }} />
                          </div>
                        </td>
                        <td className="num">{row.total}</td>
                        <td className="num">{row.hot}</td>
                        <td className="num">{row.invoice_sent}</td>
                        <td className="num">{row.paid}</td>
                        <td className="num">{row.lost}</td>
                        <td className="num"><b>{formatPercent(row.paid, row.total)}</b></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
