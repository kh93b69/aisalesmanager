-- Миграция 005: дожимы только для новых разговоров.
-- Выполнить один раз в Supabase → SQL Editor. Повторный запуск безопасен.
--
-- Момент включения дожимов: дожим получают только клиенты, которым бот написал ПОСЛЕ этого момента.
-- Старым диалогам (до включения) дожимы не уходят — никакой «рассылки по всей базе».
ALTER TABLE bots ADD COLUMN IF NOT EXISTS followups_enabled_at TIMESTAMPTZ;

-- Для ботов, у которых дожимы уже включены, считаем моментом включения «сейчас»:
-- всё, что было до миграции, дожимать не будем
UPDATE bots SET followups_enabled_at = NOW()
WHERE followups_enabled = TRUE AND followups_enabled_at IS NULL;
