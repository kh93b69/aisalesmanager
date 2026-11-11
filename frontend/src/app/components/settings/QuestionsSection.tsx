"use client";

// Настройки → ❓ Без ответа: вопросы клиентов, на которые бот не нашёл ответа в базе знаний.
// Владелец отвечает здесь — ответ сразу попадает в базу знаний (бот «дообучается»)
// и, по желанию, уходит клиенту в WhatsApp.

import { useEffect, useState } from "react";
import {
  apiDelete, apiPost, apiRequest, Bot, errorText, formatMessageTime, formatPhone,
  QuestionStatus, UnansweredQuestion,
} from "../api";

type Props = {
  bot: Bot;
};

const TABS: { key: QuestionStatus; label: string }[] = [
  { key: "open", label: "Ждут ответа" },
  { key: "answered", label: "С ответом (в базе знаний)" },
  { key: "dismissed", label: "Скрытые" },
];

// Кто спрашивал: имя или номер
function clientLabel(question: UnansweredQuestion): string {
  if (question.contact_name) return question.contact_name;
  if (question.chat_id) return formatPhone(question.chat_id);
  return "Клиент";
}

// Форма ответа на один открытый вопрос
function AnswerForm({ question, onDone }: { question: UnansweredQuestion; onDone: () => void }) {
  const [answer, setAnswer] = useState("");
  const [sendToClient, setSendToClient] = useState(Boolean(question.dialog_id));
  const [cityOnly, setCityOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!answer.trim()) {
      setError("Напишите ответ");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await apiPost(`/api/questions/${question.id}/answer`, {
        answer,
        send_to_client: sendToClient,
        city_only: cityOnly,
      });
      onDone();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  const dismiss = async () => {
    setBusy(true);
    setError("");
    try {
      await apiPost(`/api/questions/${question.id}/dismiss`);
      onDone();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  };

  return (
    <div className="question-answer">
      <textarea
        className="input"
        value={answer}
        maxLength={3000}
        rows={3}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Ответ, который бот запомнит. Например: «Да, есть рассрочка Kaspi Red на 12 месяцев без переплаты»"
        disabled={busy}
      />
      <div className="question-options">
        {question.dialog_id && (
          <label className="checkbox-row">
            <input type="checkbox" checked={sendToClient} onChange={(e) => setSendToClient(e.target.checked)} disabled={busy} />
            Отправить этот ответ клиенту в WhatsApp
          </label>
        )}
        {question.city_id && (
          <label className="checkbox-row">
            <input type="checkbox" checked={cityOnly} onChange={(e) => setCityOnly(e.target.checked)} disabled={busy} />
            Ответ только для города {question.city_name || ""}
          </label>
        )}
      </div>
      <div className="form-actions">
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy}>
          {busy ? "Сохраняем…" : "Ответить и запомнить"}
        </button>
        <button className="btn btn-outline btn-sm" onClick={dismiss} disabled={busy}>Скрыть</button>
        {error && <span className="text-danger">{error}</span>}
      </div>
    </div>
  );
}

export default function QuestionsSection({ bot }: Props) {
  const [tab, setTab] = useState<QuestionStatus>("open");
  const [questions, setQuestions] = useState<UnansweredQuestion[]>([]);
  const [openCount, setOpenCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Перезагрузка списка после ответа/удаления
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    apiRequest<{ questions: UnansweredQuestion[]; open_count: number }>(`/api/bots/${bot.id}/questions?status=${tab}`)
      .then((data) => {
        if (cancelled) return;
        setQuestions(data.questions || []);
        setOpenCount(data.open_count || 0);
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
  }, [bot.id, tab, reloadKey]);

  const reload = () => setReloadKey((value) => value + 1);

  const removeQuestion = async (question: UnansweredQuestion) => {
    const text = question.status === "answered"
      ? "Удалить вопрос? Ответ тоже уберётся из базы знаний — бот перестанет его знать."
      : "Удалить вопрос?";
    if (!confirm(text)) return;
    try {
      await apiDelete(`/api/questions/${question.id}`);
      reload();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="settings-section">
      <h3>Вопросы без ответа</h3>
      <p>Когда в базе знаний нет ответа, бот не выдумывает: говорит клиенту, что уточнит у руководителя,
        и присылает вам уведомление в Telegram. Ответьте здесь — бот запомнит ответ и дальше будет отвечать сам.</p>

      <div className="question-tabs">
        {TABS.map((t) => (
          <button key={t.key} className={`btn btn-sm ${tab === t.key ? "btn-primary" : "btn-outline"}`} onClick={() => setTab(t.key)}>
            {t.label}{t.key === "open" && openCount > 0 ? ` (${openCount})` : ""}
          </button>
        ))}
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {loading && questions.length === 0 && <div className="text-muted"><span className="spinner" /> Загружаем…</div>}
      {!loading && questions.length === 0 && (
        <div className="empty-box">
          {tab === "open" ? "Новых вопросов нет — бот знает ответы на всё, о чём спрашивали 👍" : "Здесь пока пусто"}
        </div>
      )}

      {questions.map((question) => (
        <div key={question.id} className="question-card">
          <div className="question-head">
            <div className="question-text">❓ {question.question}</div>
            <button className="btn btn-sm btn-ghost-danger" onClick={() => removeQuestion(question)} title="Удалить">✕</button>
          </div>
          <div className="question-meta">
            {clientLabel(question)}
            {question.city_name ? ` · 📍 ${question.city_name}` : ""}
            {" · "}{formatMessageTime(question.created_at)}
            {question.dialog_id && (
              <> · <a href={`/?dialog=${encodeURIComponent(question.dialog_id)}`}>открыть диалог</a></>
            )}
          </div>
          {question.client_message && question.client_message !== question.question && (
            <div className="question-client">Клиент написал: «{question.client_message}»</div>
          )}
          {question.status === "open" && <AnswerForm question={question} onDone={reload} />}
          {question.status === "answered" && (
            <div className="question-saved">✓ Ответ в базе знаний: {question.answer}</div>
          )}
        </div>
      ))}
    </div>
  );
}
