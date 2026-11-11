# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Инструкции

- Всегда отвечать на русском языке
- Писать комментарии в коде на русском
- Объяснять ошибки и предложения на русском
- Используй Sequential Thinking для сложных размышлений
- Никогда не используй наследование, присваивание классу внешних функций, рефлексию и другие сложные техники. Код должен быь понятен Junior разработчику с минимальным опытом
- Используй Context7 для досткупа к документации всех библиотек
- Для реализации любых фич с использованием интеграций с внешним api/библиотеками изучай документации с помощью Context7 инструментов
- Если есть изменения на фронтенде, то проверь что фронт работает, открыв его через Playwright

## Проект

AI Sales Manager — сервис ИИ-менеджеров продаж в WhatsApp (через Wazzup) для малого и среднего бизнеса. Бот консультирует по базе знаний, ведёт клиента к целевому действию и передаёт «горячих» клиентов сотрудникам (счёт/заказ оформляет человек). Промпт собирается из анкеты компании (тип бизнеса general/clinic, название, описание, целевое действие); для сетей в нескольких городах бот выясняет город и отвечает по базе этого города. В настройках есть «Тест бота» — переписка с ботом без WhatsApp.

## Стек

- **Backend:** Python 3.12 + FastAPI
- **Frontend:** Next.js 16 + React 19 + TypeScript (static export, раздаётся бэкендом)
- **БД:** Supabase (PostgreSQL)
- **AI:** Claude API (Anthropic SDK 2.x) — по умолчанию `claude-haiku-5-5`, опционально `claude-sonnet-5-5` на бота; structured outputs (JSON-схема ответа)
- **WhatsApp:** Wazzup API v3 (один номер на бота, вебхук `/webhook/wazzup/{bot_id}?token=…`)
- **Уведомления сотрудникам:** Telegram Bot API
- **Голосовые:** OpenAI `gpt-4o-mini-transcribe` (необязательно)
- **Хостинг:** Railway (авто-деплой из GitHub, 1 реплика — планировщик и замки внутри процесса)
- **Репозиторий:** github.com/kh93b69/aisalesmanager

## Команды

```bash
# Локальная разработка (Docker)
docker-compose up

# Бэкенд отдельно
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# Тесты бэкенда (без сети; фиктивные ключи Supabase задаются в самих тестах)
cd backend && python -m pytest tests -q

# Фронтенд отдельно
cd frontend && npm install && npm run dev
```

Запуск для клиента и чек-лист — `SETUP.md`. Миграции БД — `backend/migrations/*.sql` (применяются вручную в Supabase SQL Editor).

## Архитектура

```
backend/app/
  main.py         — точка входа FastAPI, CORS, роуты, планировщик (lifespan), раздача фронтенда
  config.py       — переменные окружения
  database.py     — клиент Supabase (service role)
  auth.py         — проверка JWT Supabase, get_current_user / get_current_admin
  access.py       — права: владелец бота / менеджер команды (с привязкой к городу)
  pipeline.py     — ядро: входящее сообщение → debounce → город → база знаний → Claude → ответ,
                    горячий лид, автопауза ИИ при ответе сотрудника, лимит подписки
  ai_engine.py    — вызов Claude (structured outputs: reply, detected_city, lead_status, need_human…)
  prompts.py      — системный промпт из анкеты компании; режим clinic добавляет медицинские правила (2 блока с кэшем)
  rag.py          — база знаний города (+ общая): целиком, если небольшая, иначе поиск
  cities.py       — определение города по тексту (падежи, алиасы)
  wazzup.py       — клиент Wazzup API (каналы, вебхук, отправка)
  messenger.py    — отправка клиенту в нужный канал (WhatsApp/Telegram), теги [IMAGE:url]
  notifier.py     — уведомления в Telegram (горячий клиент, напоминания, внимание)
  scheduler.py    — напоминания о счёте (каждые 60 с)
  transcribe.py   — расшифровка голосовых
  telegram.py     — Telegram Bot API (канал для клиентов)
  routes/
    webhooks.py   — вебхуки Wazzup и Telegram
    dialogs.py    — диалоги, статусы сделки, отправка сообщения сотрудником, статистика
    settings.py   — боты (анкета компании), Wazzup, города, база знаний, картинки, команда, уведомления
    test_chat.py  — тестовый чат с ботом из панели (ничего не сохраняет и не отправляет)
    auth.py, admin.py

frontend/src/app/
  page.tsx                 — каркас панели (боты, Диалоги / Статистика / Настройки)
  components/api.ts        — authFetch (с обновлением токена), типы
  components/DialogList.tsx, ChatView.tsx, StatsView.tsx, SettingsView.tsx, settings/*
```

## Таблицы Supabase

- **bots** — настройки бота: анкета (business_type, company_name, assistant_name, business_description, target_action), system_prompt, ai_model, wazzup_* (ключ и секрет не отдаются на фронт), уведомления
- **cities** — города/филиалы бота (name, aliases, address, telegram_chat_id)
- **team_members** — сотрудники бота (user_id, city_id — NULL = все города)
- **dialogs** — диалоги: city_id, status (new → in_progress → hot → invoice_sent → paid / lost), ai_disabled, needs_attention, hot_at…
- **messages** — сообщения (role: user / assistant / manager, external_id — защита от дублей)
- **knowledge_chunks** — фрагменты базы знаний (city_id NULL = общая)
- **users**, **subscriptions** — аккаунты и лимиты

## Переменные окружения

Задаются в `.env` (локально) и в Railway Dashboard (продакшн), полный список — `.env.example`:
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, TELEGRAM_NOTIFY_BOT_TOKEN, BACKEND_URL,
OPENAI_API_KEY (голосовые), TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_ID (Telegram-канал), DEFAULT_AI_MODEL, REPLY_DEBOUNCE_SECONDS, TIMEZONE
