-- ============================================================
-- Миграция 001: Wazzup, анкета компании, города, статусы сделок, команда, уведомления
-- Дата: 2026-11-10
--
-- Как применить: Supabase Dashboard → SQL Editor → вставить файл целиком → Run.
-- Миграцию можно запускать повторно: всё через IF NOT EXISTS / DROP IF EXISTS.
-- Старые данные не удаляются.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Боты: подключение Wazzup, модель ИИ, уведомления
-- ------------------------------------------------------------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS wazzup_api_key TEXT;                -- API-ключ Wazzup (секрет, не отдаём на фронт)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS wazzup_channel_id TEXT;             -- ID канала WhatsApp в Wazzup (uuid)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS wazzup_channel_phone TEXT;          -- номер телефона канала (для отображения)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS wazzup_channel_state TEXT;          -- статус канала: active, qr, phoneUnavailable...
ALTER TABLE bots ADD COLUMN IF NOT EXISTS wazzup_webhook_secret TEXT;         -- секрет в URL вебхука (секрет, не отдаём на фронт)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS ai_model TEXT NOT NULL DEFAULT 'claude-haiku-5-5';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS telegram_notify_chat_id TEXT;       -- общий чат уведомлений (руководитель)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS invoice_reminder_minutes INTEGER NOT NULL DEFAULT 10;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS assignment_mode TEXT NOT NULL DEFAULT 'none';  -- none | round_robin

-- Telegram-канал теперь привязывается только через переменную TELEGRAM_BOT_ID (Railway).
-- Флаг telegram_enabled больше не используется — сбрасываем, чтобы старые значения ничего не значили.
ALTER TABLE bots ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE bots SET telegram_enabled = FALSE WHERE telegram_enabled IS TRUE;

-- Анкета компании: из неё собирается промпт (подходит для любого бизнеса)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS business_type TEXT NOT NULL DEFAULT 'general';  -- general | clinic
ALTER TABLE bots ADD COLUMN IF NOT EXISTS company_name TEXT NOT NULL DEFAULT '';          -- «Мебель Плюс»
ALTER TABLE bots ADD COLUMN IF NOT EXISTS assistant_name TEXT NOT NULL DEFAULT '';        -- как зовут ассистента
ALTER TABLE bots ADD COLUMN IF NOT EXISTS business_description TEXT NOT NULL DEFAULT '';  -- чем занимается компания
ALTER TABLE bots ADD COLUMN IF NOT EXISTS target_action TEXT NOT NULL DEFAULT '';         -- когда клиент «горячий»

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_business_type_check;
ALTER TABLE bots ADD CONSTRAINT bots_business_type_check
  CHECK (business_type IN ('general', 'clinic'));

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_assignment_mode_check;
ALTER TABLE bots ADD CONSTRAINT bots_assignment_mode_check
  CHECK (assignment_mode IN ('none', 'round_robin'));

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_ai_model_check;
ALTER TABLE bots ADD CONSTRAINT bots_ai_model_check
  CHECK (ai_model IN ('claude-haiku-5-5', 'claude-sonnet-5-5'));


-- ------------------------------------------------------------
-- 2. Города (филиалы) бота
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  name TEXT NOT NULL,                          -- "Астана"
  aliases TEXT NOT NULL DEFAULT '',            -- через запятую: "нур-султан, столица"
  address TEXT NOT NULL DEFAULT '',            -- адрес филиала, телефон, график — коротко
  telegram_chat_id TEXT,                       -- чат уведомлений этого города
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cities_bot ON cities(bot_id, sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cities_bot_name ON cities(bot_id, lower(name));
ALTER TABLE cities ENABLE ROW LEVEL SECURITY;


-- ------------------------------------------------------------
-- 3. База знаний: привязка к городу (NULL = общая для всех городов)
-- ------------------------------------------------------------
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS city_id UUID REFERENCES cities(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_knowledge_bot_city ON knowledge_chunks(bot_id, city_id);


-- ------------------------------------------------------------
-- 4. Команда: менеджеры и администраторы филиалов
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bot_id UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,                       -- id из таблицы users
  -- NULL = видит все города. При удалении города его сотрудники удаляются из команды
  -- (иначе они внезапно получили бы доступ ко ВСЕМ городам).
  city_id UUID REFERENCES cities(id) ON DELETE CASCADE,
  telegram_chat_id TEXT,                       -- личные уведомления сотрудника
  last_assigned_at TIMESTAMPTZ,                -- для распределения «по очереди»
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_bot_user ON team_members(bot_id, user_id);
CREATE INDEX IF NOT EXISTS idx_team_user ON team_members(user_id);
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;


-- ------------------------------------------------------------
-- 5. Диалоги: город, статус сделки, контакт, напоминания
-- ------------------------------------------------------------
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS city_id UUID REFERENCES cities(id) ON DELETE SET NULL;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new';
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS contact_name TEXT;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS hot_reason TEXT;               -- что хочет клиент (из ИИ)
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS needs_attention BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS attention_reason TEXT;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS assigned_user_id UUID;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS hot_at TIMESTAMPTZ;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS invoice_sent_at TIMESTAMPTZ;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMPTZ;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS last_message_preview TEXT;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS last_manager_message_at TIMESTAMPTZ;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS reminder_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS ai_paused_reason TEXT;         -- manager_replied | manual | limit

ALTER TABLE dialogs DROP CONSTRAINT IF EXISTS dialogs_status_check;
ALTER TABLE dialogs ADD CONSTRAINT dialogs_status_check
  CHECK (status IN ('new', 'in_progress', 'hot', 'invoice_sent', 'paid', 'lost'));

-- Для старых диалогов: время последнего сообщения = время создания
UPDATE dialogs SET last_message_at = created_at WHERE last_message_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_dialogs_bot_last ON dialogs(bot_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_dialogs_bot_status ON dialogs(bot_id, status);
CREATE INDEX IF NOT EXISTS idx_dialogs_bot_chat ON dialogs(bot_id, channel, chat_id);
CREATE INDEX IF NOT EXISTS idx_dialogs_hot ON dialogs(hot_at) WHERE status = 'hot';


-- ------------------------------------------------------------
-- 6. Сообщения: роль manager, внешний ID (защита от дублей), медиа
-- ------------------------------------------------------------
-- Снимаем ЛЮБЫЕ старые CHECK-ограничения на колонку role (имя могло быть нестандартным),
-- иначе вставка role='manager' упадёт
DO $$
DECLARE
  constraint_row RECORD;
BEGIN
  FOR constraint_row IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'messages'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%role%'
  LOOP
    EXECUTE format('ALTER TABLE messages DROP CONSTRAINT %I', constraint_row.conname);
  END LOOP;
END $$;
ALTER TABLE messages ADD CONSTRAINT messages_role_check
  CHECK (role IN ('user', 'assistant', 'manager'));

ALTER TABLE messages ADD COLUMN IF NOT EXISTS external_id TEXT;    -- messageId из Wazzup
ALTER TABLE messages ADD COLUMN IF NOT EXISTS message_type TEXT NOT NULL DEFAULT 'text';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS author_name TEXT;    -- кто из сотрудников написал
ALTER TABLE messages ADD COLUMN IF NOT EXISTS send_error TEXT;     -- ошибка отправки клиенту, если была

-- Один и тот же messageId из Wazzup не сохранится дважды
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id
  ON messages(external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_dialog_created ON messages(dialog_id, created_at);


-- ------------------------------------------------------------
-- 7. Подписка: для клиники с 1500–3000 заявок поднимите лимит в админке.
-- Значение по умолчанию (100) оставляем как было.
-- ------------------------------------------------------------
