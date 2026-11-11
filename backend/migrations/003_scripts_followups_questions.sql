-- ============================================================
-- Миграция 003: скрипты (приветствие, скрипт продаж), ссылки, дожимы, вопросы без ответа
-- Дата: 2026-11-12
--
-- Как применить: Supabase Dashboard → SQL Editor → вставить файл целиком → Run.
-- Можно запускать повторно. Старые данные не удаляются.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Бот: скрипты, ссылки, дожимы
-- ------------------------------------------------------------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS greeting_message TEXT NOT NULL DEFAULT '';   -- первое сообщение бота
ALTER TABLE bots ADD COLUMN IF NOT EXISTS sales_script TEXT NOT NULL DEFAULT '';       -- скрипт продаж, ответы на возражения
-- Ссылки, которые бот отправляет по ситуации: [{"title": "Каталог", "url": "https://...", "when": "просят каталог"}]
ALTER TABLE bots ADD COLUMN IF NOT EXISTS links JSONB NOT NULL DEFAULT '[]'::jsonb;
-- Дожимы: включены ли и шаги [{"hours": 3, "text": ""}] (пустой текст — ИИ напишет сам по контексту)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS followups_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS followups JSONB NOT NULL DEFAULT '[]'::jsonb;


-- ------------------------------------------------------------
-- 2. Диалог: сколько дожимов уже отправлено (сбрасывается, когда клиент ответил)
-- ------------------------------------------------------------
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS followup_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS last_followup_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_dialogs_followup
  ON dialogs(bot_id, last_message_at)
  WHERE status IN ('new', 'in_progress') AND ai_disabled = FALSE;


-- ------------------------------------------------------------
-- 3. Вопросы, на которые бот не знал ответа (владелец отвечает → ответ попадает в базу знаний)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unanswered_questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  dialog_id UUID REFERENCES dialogs(id) ON DELETE SET NULL,
  city_id UUID REFERENCES cities(id) ON DELETE SET NULL,
  question TEXT NOT NULL,                       -- вопрос клиента (коротко, как понял ИИ)
  client_message TEXT NOT NULL DEFAULT '',      -- исходное сообщение клиента
  status TEXT NOT NULL DEFAULT 'open',          -- open | answered | dismissed
  answer TEXT NOT NULL DEFAULT '',
  knowledge_chunk_id TEXT,                      -- id фрагмента базы знаний с ответом
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  answered_at TIMESTAMPTZ
);

ALTER TABLE unanswered_questions DROP CONSTRAINT IF EXISTS unanswered_questions_status_check;
ALTER TABLE unanswered_questions ADD CONSTRAINT unanswered_questions_status_check
  CHECK (status IN ('open', 'answered', 'dismissed'));

CREATE INDEX IF NOT EXISTS idx_unanswered_bot_status ON unanswered_questions(bot_id, status, created_at DESC);
ALTER TABLE unanswered_questions ENABLE ROW LEVEL SECURITY;
