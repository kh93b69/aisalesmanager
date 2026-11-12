"use client";

// Настройки → WhatsApp (Wazzup): ключ API → выбор канала → подключение → статус.

import { useEffect, useState } from "react";
import {
  apiPost,
  apiRequest,
  Bot,
  errorText,
  formatPhone,
  WazzupChannel,
  wazzupStateInfo,
  WazzupStatus,
} from "../api";

type Props = {
  bot: Bot;
  onBotUpdated: (bot: Bot) => void;
};

export default function WazzupSection({ bot, onBotUpdated }: Props) {
  const [status, setStatus] = useState<WazzupStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [channels, setChannels] = useState<WazzupChannel[] | null>(null);
  // Ключ, который прошёл проверку. Сервер его не сохраняет до выбора канала,
  // поэтому передаём его вместе с channel_id
  const [verifiedKey, setVerifiedKey] = useState("");
  // Показать форму ввода ключа, даже если уже подключено (смена ключа/канала)
  const [showSetup, setShowSetup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  const loadStatus = async () => {
    setStatusLoading(true);
    try {
      const data = await apiRequest<WazzupStatus>(`/api/bots/${bot.id}/wazzup/status`);
      setStatus(data);
    } catch (err) {
      setError(errorText(err));
    }
    setStatusLoading(false);
  };

  // Первая загрузка статуса. cancelled — чтобы ответ для старого бота не попал к новому
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setChannels(null);
    setVerifiedKey("");
    setApiKey("");
    setShowSetup(false);
    setError("");
    setInfo("");
    setStatusLoading(true);
    apiRequest<WazzupStatus>(`/api/bots/${bot.id}/wazzup/status`)
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorText(err));
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bot.id]);

  // Отмена смены ключа: возвращаемся к текущему подключению, ничего не меняя на сервере
  const cancelSetup = () => {
    setShowSetup(false);
    setChannels(null);
    setVerifiedKey("");
    setApiKey("");
    setError("");
  };

  // Шаг 1: проверить ключ и получить список каналов WhatsApp
  const checkKey = async () => {
    if (!apiKey.trim()) {
      setError("Вставьте API-ключ Wazzup");
      return;
    }
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const data = await apiPost<{ ok: boolean; channels?: WazzupChannel[]; error?: string }>(
        `/api/bots/${bot.id}/wazzup/connect`,
        { api_key: apiKey.trim() }
      );
      if (!data.ok) {
        setError(data.error || "Ключ не подошёл");
        setChannels(null);
        setVerifiedKey("");
      } else {
        setChannels(data.channels || []);
        setVerifiedKey(apiKey.trim());
        setApiKey("");
        if (!data.channels || data.channels.length === 0) {
          setError("Ключ верный, но в Wazzup нет каналов WhatsApp. Добавьте канал в личном кабинете Wazzup.");
        }
      }
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  // Сменить номер: список каналов по уже сохранённому ключу (ключ вводить заново не нужно)
  const loadSavedChannels = async () => {
    setBusy(true);
    setError("");
    setInfo("");
    try {
      const data = await apiPost<{ ok: boolean; channels?: WazzupChannel[]; error?: string }>(
        `/api/bots/${bot.id}/wazzup/connect`,
        {}
      );
      if (!data.ok) {
        setError(data.error || "Не удалось получить номера из Wazzup");
      } else {
        setShowSetup(true);
        setChannels(data.channels || []);
        setVerifiedKey("");
      }
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  // Шаг 2: подключить выбранный канал (бэкенд сохранит ключ и сам настроит вебхук).
  // useKey=false — переподключение текущего канала со старым ключом (передаём только channel_id)
  const connectChannel = async (channelId: string, useKey: boolean) => {
    setBusy(true);
    setError("");
    setInfo("");
    const body: { channel_id: string; api_key?: string } = { channel_id: channelId };
    if (useKey && verifiedKey) body.api_key = verifiedKey;
    try {
      const data = await apiPost<{ ok: boolean; error?: string; bot?: Bot }>(
        `/api/bots/${bot.id}/wazzup/channel`,
        body
      );
      if (data.bot) {
        onBotUpdated({ ...bot, ...data.bot });
      } else if (data.ok) {
        onBotUpdated({ ...bot, wazzup_connected: true, wazzup_channel_id: channelId });
      }
      if (!data.ok) {
        setError(data.error || "Не удалось подключить канал");
      } else {
        setInfo("Канал подключён. Новые сообщения из WhatsApp будут приходить сюда.");
        setChannels(null);
        setVerifiedKey("");
        setShowSetup(false);
      }
      await loadStatus();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const disconnect = async () => {
    if (!confirm("Отключить WhatsApp? Бот перестанет получать и отправлять сообщения.")) return;
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await apiPost(`/api/bots/${bot.id}/wazzup/disconnect`);
      onBotUpdated({
        ...bot,
        wazzup_connected: false,
        wazzup_channel_id: null,
        wazzup_channel_phone: null,
        wazzup_channel_state: null,
      });
      setInfo("WhatsApp отключён");
      await loadStatus();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const connected = status?.connected || false;
  const stateInfo = wazzupStateInfo(status?.state);

  return (
    <div className="settings-section">
      <h3>WhatsApp через Wazzup</h3>
      <p>Бот получает и отправляет сообщения WhatsApp через сервис Wazzup.</p>

      {/* Текущий статус */}
      <div className="status-box">
        {statusLoading && !status ? (
          <span className="text-muted"><span className="spinner" /> Проверяем подключение…</span>
        ) : connected ? (
          <>
            <div className="status-line">
              <span className={`wa-status-badge ${stateInfo.cls}`}>{stateInfo.text}</span>
              {status?.phone && <span className="status-phone">{formatPhone(status.phone)}</span>}
            </div>
            <div className="status-line text-muted">
              {status?.webhook_ok
                ? "✓ Вебхук настроен — сообщения приходят в панель"
                : "⚠ Вебхук не настроен — нажмите «Переподключить канал»"}
            </div>
            {status?.state === "qr" && (
              <div className="hint">
                Откройте личный кабинет Wazzup → Каналы → нужный канал и отсканируйте QR-код
                телефоном с WhatsApp (Настройки → Связанные устройства).
              </div>
            )}
          </>
        ) : (
          <span className="wa-status-badge wa-disconnected">Не подключено</span>
        )}
      </div>

      <div className="form-actions" style={{ marginBottom: 16 }}>
        <button className="btn btn-sm btn-outline" onClick={loadStatus} disabled={busy || statusLoading}>
          {statusLoading ? "Обновляем…" : "Обновить статус"}
        </button>
        {connected && status?.channel_id && !status.webhook_ok && (
          <button className="btn btn-sm btn-primary" onClick={() => connectChannel(status.channel_id as string, false)} disabled={busy}>
            Переподключить канал
          </button>
        )}
        {connected && !showSetup && (
          <button className="btn btn-sm btn-outline" onClick={loadSavedChannels} disabled={busy}>
            Выбрать другой номер
          </button>
        )}
        {connected && !showSetup && (
          <button className="btn btn-sm btn-outline" onClick={() => setShowSetup(true)} disabled={busy}>
            Сменить ключ Wazzup
          </button>
        )}
        {connected && (
          <button className="btn btn-sm btn-ghost-danger" onClick={disconnect} disabled={busy}>
            Отключить
          </button>
        )}
      </div>

      {/* Подключение: ключ → каналы */}
      {(!connected || showSetup) && (
        <div className="setup-steps">
          <div className="form-group">
            <label>1. API-ключ Wazzup</label>
            <div className="input-row">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Ключ из Wazzup: «Интеграция с CRM» → API → «Подключить»"
                autoComplete="off"
              />
              <button className="btn btn-primary" onClick={checkKey} disabled={busy}>
                {busy && !channels ? "Проверяем…" : "Проверить ключ"}
              </button>
            </div>
            <div className="hint">Ключ хранится на сервере и не показывается в браузере.</div>
          </div>

          {channels && channels.length > 0 && (
            <div className="form-group">
              <label>2. Выберите номер WhatsApp для этого бота</label>
              <div className="hint" style={{ marginBottom: 8 }}>Один номер — один бот. Для второго номера создайте ещё одного бота
                (кнопка «+ Бот» вверху) и выберите номер там — ключ Wazzup тот же.</div>
              <div className="channel-list">
                {channels.map((ch) => {
                  const chState = wazzupStateInfo(ch.state);
                  return (
                    <div key={ch.channelId} className="channel-row">
                      <div>
                        <div className="channel-phone">{ch.plainId ? formatPhone(ch.plainId) : ch.channelId}</div>
                        <span className={`wa-status-badge wa-small ${chState.cls}`}>{chState.text}</span>
                        {ch.used_by && <div className="channel-used">Подключён к боту «{ch.used_by}»</div>}
                      </div>
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => connectChannel(ch.channelId, true)}
                        disabled={busy || Boolean(ch.used_by)}
                      >
                        {status?.channel_id === ch.channelId ? "Переподключить" : ch.used_by ? "Занят" : "Подключить"}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {showSetup && (
            <button className="btn btn-sm btn-outline" onClick={cancelSetup}>
              Отмена
            </button>
          )}
        </div>
      )}

      {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
      {info && <div className="alert alert-success" style={{ marginTop: 12 }}>{info}</div>}
    </div>
  );
}
