# Jarvis backend (MVP: этапы 1–2)

Backend ИИ-ассистента умного дома. Полное ТЗ — в [docs/TZ.md](docs/TZ.md);
этот README покрывает только то, что реально реализовано на данный момент:
цикл tool use на Claude API поверх Home Assistant с mock-сущностями на 4
комнаты.

## Архитектура

```
Жилец (голос / iPad)            <- пока не реализовано, см. "Что отложено"
        │
        ▼
Voice-agent платформа            <- пока не реализовано
   STT → [ Jarvis backend ] → TTS
        │
        ▼
Jarvis backend (Claude API, tool use)      <- ЭТО ЕСТЬ
   ├── Слой данных     — Home Assistant (состояние + история)
   ├── Слой контекста  — снимок дома по запросу (get_home_status)
   ├── Слой внимания   — rule-engine на порогах               <- пока не реализовано
   └── Слой памяти     — предпочтения семьи                    <- пока не реализовано
        │
        ▼
Home Assistant → mock-сущности по 4 комнатам (реальные устройства позже)
```

Jarvis не получает поток сырых показаний датчиков — он либо сам запрашивает
снимок состояния через `get_home_status`, либо (в будущем) его будит слой
внимания. Подробное обоснование каждого решения — в [docs/TZ.md](docs/TZ.md)
разделы 4–5.

## Структура репозитория

```
docker-compose.yml              # локальный Home Assistant
homeassistant/config/           # HA-конфиг: helpers + template-сенсоры + скрипты
backend/
  app/
    config.py                   # настройки из .env
    ha_client.py                # обёртка над HA REST API
    tools.py                    # схемы инструментов + dispatch + правила безопасности
    prompts.py                  # системный промпт, собирается из живого снимка HA
    agent.py                    # цикл tool use с Claude API (на сессию)
    main.py                     # FastAPI: POST /chat
  chat_cli.py                   # локальный REPL без HTTP
  tests/
    test_tools.py               # тесты правил безопасности
    test_agent.py               # тесты самого tool-use цикла (моки)
docs/TZ.md                      # полное техническое задание
```

## Почему mock-сущности сделаны через `input_number`/`input_boolean`/`template`, а не через `demo:`

Встроенная демо-платформа Home Assistant создаёт фиксированный набор
демонстрационных устройств и не даёт завести кастомные CO2/влажность на
конкретную комнату с нужными именами. `input_number`/`input_boolean` —
такие же штатные хелперы HA (не наш кастомный код), но полностью
управляемые: под каждую комнату заведены сенсоры температуры/влажности/CO2,
целевая температура, свет, кондиционер и вентиляция — то есть ровно то, что
нужно инструментам Jarvis, без придумывания собственного протокола.

## Как поднять локально

1. **Home Assistant.** Нужен запущенный Docker Desktop, затем из корня репозитория:

   ```bash
   docker compose up -d
   ```

   Откройте http://localhost:8123 и пройдите разовый onboarding (создать
   администратора — HA требует это один раз через UI, скриптом не обойти).

2. **Long-lived access token.** В HA: иконка профиля → вкладка Security →
   "Long-Lived Access Tokens" → Create Token. Скопируйте его.

3. **Секреты.** Скопируйте `.env.example` в `.env` в корне репозитория и
   заполните:
   - `ANTHROPIC_API_KEY` — ключ Claude API
   - `HOME_ASSISTANT_TOKEN` — токен из шага 2

4. **Зависимости backend:**

   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

5. **Запуск:**

   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

   или для быстрой ручной проверки без HTTP:

   ```bash
   python chat_cli.py
   ```

6. **Пример запроса:**

   ```bash
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id": "test", "message": "включи свет в спальне"}'
   ```

   Ответ: `{"response": "...", "actions": [{"tool": "set_room_lights", "input": {...}, "result": {...}}]}`.

## Как гонять тесты

```bash
cd backend
pytest
```

Все тесты изолированы от внешнего мира:
- `test_tools.py` проверяет только код проверки диапазонов/подтверждений —
  сетевые вызовы к HA либо не происходят вообще (reject-пути возвращаются
  раньше), либо замоканы через `unittest.mock.AsyncMock`.
- `test_agent.py` проверяет сам tool-use цикл с замоканным Anthropic-клиентом
  и замоканными `dispatch`/`build_system_prompt` — реального обращения к
  Claude API или Home Assistant нет.

Ключи и запущенный Home Assistant для `pytest` не нужны — только для
реального end-to-end прогона через `uvicorn`/`chat_cli.py`.

## Что реализовано (этапы 1–2 из ТЗ)

Соответствует Definition of Done в [docs/TZ.md](docs/TZ.md#8-definition-of-done-для-текущего-mvp-этапы-1–2):

- [x] HA поднимается локально в Docker с mock-сущностями по 4 комнатам,
      конфиг проходит `check_config`/`docker compose config`.
- [x] Backend держит цикл tool use с Claude API, инструменты — прямые
      вызовы HA REST API.
- [x] `POST /chat` → `{response, actions}`.
- [x] Правила безопасности (диапазон температуры, multi-room confirmation)
      проверены юнит-тестами, а не только описаны в промпте.
- [x] Нет инструмента управления O2.
- [x] Тесты самого tool-use цикла на моках, без реальных API-вызовов.

## Что сознательно отложено

Со ссылкой на [docs/TZ.md раздел 7](docs/TZ.md#7-этапы-разработки) — это
следующие этапы, не входящие в MVP:

- **Слой внимания** (rule-engine на порогах, проактивные реплики Jarvis) —
  этап 3, требует отдельного проектирования событийной модели.
- **Хранилище памяти/предпочтений семьи** — этап 4, сознательно после
  backend-скелета, чтобы не проектировать схему БД до того, как понятно,
  какие данные реально нужны диалогам.
- **Голосовой пайплайн** (LiveKit Agents/Vapi/Retell + ElevenLabs) — этап 5,
  сознательно отложен: backend спроектирован как чёрный ящик
  `текст → {response, actions}`, к которому voice-платформа подключится
  позже без изменений в tool-use логике.
- **iPad-приложение** (Apple Business Manager + MDM kiosk) — этап 6, не
  блокирует backend-разработку и требует отдельного трека (сертификаты,
  MDM-провайдер).
