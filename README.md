# Jarvis backend (фаза 0: ядро ИИ)

Backend ИИ-ассистента умного дома. Полное ТЗ — в [docs/TZ.md](docs/TZ.md);
этот README покрывает то, что реально реализовано на данный момент: **ядро,
не привязанное ни к конкретной LLM, ни к какому-либо устройству**. Home
Assistant в этой фазе не нужен вообще — ни одного доменного тула ещё не
зарегистрировано.

## Архитектура

```
Жилец (голос / iPad)                                    <- пока не реализовано
        │
        ▼
Voice-agent платформа                                    <- пока не реализовано
   STT → [ Jarvis backend ] → TTS
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│ Jarvis backend                                             │
│                                                              │
│  Ядро (device-agnostic И llm-agnostic)         <- ЭТО ЕСТЬ  │
│   ├── цикл tool use через LLMProvider-адаптер               │
│   │     (llm/claude.py — дефолтная реализация)              │
│   ├── ToolRegistry — пуст, ждёт доменных тулов              │
│   ├── персона: butler / warm / adaptive                     │
│   └── память: SQLite (настройки дома + предпочтения          │
│         по resident_id)                                      │
│                                                              │
│  Реестр доменных тулов                     <- пока пустой   │
│  Слой внимания                             <- пока не реализовано │
└──────────────────────────────────────────────────────────┘
        │
        ▼
Home Assistant → появится в фазе 1, когда зарегистрируется первый домен
```

Ключевой принцип (см. [docs/TZ.md §4](docs/TZ.md)): ядро гоняет цикл tool use
над реестром тулов и над LLM-провайдером, которые ему подставляют снаружи —
сегодня реестр пуст, завтра туда добавляется домен `light`, и ядро не
меняется ни строчкой; то же верно и для смены LLM-провайдера.

Jarvis отвечает на любые вопросы, не только про дом — это часть ядра, а не
следствие наличия домашних тулов: пустой `ToolRegistry` тому доказательство
(см. тесты в `test_agent.py`).

## Структура репозитория

```
docker-compose.yml / homeassistant/config/   # HA-конфиг с прошлого прототипа,
                                              # не используется до фазы 1
backend/
  app/
    config.py                # настройки из .env (включая LLM_PROVIDER, JARVIS_DB_PATH)
    db.py                    # SQLite bootstrap (house_settings, residents, preferences)
    memory.py                # MemoryStore: персона/проактивность дома + предпочтения жильца
    persona.py                # 3 режима характера, adaptive — эвристика стиля общения (EMA)
    llm/
      base.py                  # LLMProvider protocol, ToolDef/ContentBlock/LLMResponse
      claude.py                 # ClaudeProvider — единственная реализация, дефолт
    tools/
      registry.py                # ToolRegistry + TurnContext, пока без единого домена
    agent.py                     # JarvisAgent — сам цикл tool use
    main.py                      # FastAPI: POST /chat, GET/PUT /settings
  chat_cli.py                   # локальный REPL без HTTP
  tests/                        # test_llm_claude, test_registry, test_memory,
                                 # test_persona, test_agent — все на моках/tmp_path
  ha_client.py                  # (сохранён на будущее, не импортируется пока нигде)
docs/TZ.md                      # полное техническое задание
```

## Как поднять локально

Home Assistant не нужен — только Claude API ключ.

1. **Секреты.** Скопируйте `.env.example` в `.env` в корне репозитория и
   заполните `ANTHROPIC_API_KEY`.

2. **Зависимости backend:**

   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Запуск:**

   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

   или для быстрой ручной проверки без HTTP:

   ```bash
   python chat_cli.py
   ```

4. **Пример запроса** (`resident_id` — заглушка вместо голосовой идентификации,
   см. [docs/TZ.md §5](docs/TZ.md)):

   ```bash
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id": "test", "resident_id": "ivan", "message": "какая столица Франции?"}'
   ```

   Ответ: `{"response": "Париж.", "actions": []}` — `actions` пуст, потому что
   ни одного тула ещё не зарегистрировано; это ожидаемо для фазы 0.

5. **Настройки дома** (персона/проактивность — до появления iPad-приложения):

   ```bash
   curl -X PUT http://localhost:8000/settings \
     -H "Content-Type: application/json" \
     -d '{"persona_mode": "butler"}'
   ```

## Как гонять тесты

```bash
cd backend
pytest
```

Все тесты изолированы от внешнего мира — ни разу не идут в сеть и не трогают
рабочую БД:
- `test_llm_claude.py` — `ClaudeProvider` мокает HTTP-вызов `AsyncAnthropic`,
  проверяет только перевод схем/content-блоков в обе стороны.
- `test_registry.py` — `ToolRegistry` тестируется на фейковом `echo`-туле, без
  единого упоминания Home Assistant.
- `test_memory.py`, `test_persona.py` — работают на временной SQLite
  (`tmp_path`), не на `backend/jarvis.db`.
- `test_agent.py` — `JarvisAgent` тестируется через `FakeLLM` (не наследует
  ничего от Anthropic) и либо пустой, либо однотуловый `ToolRegistry`.

Ключи и запущенный Home Assistant для `pytest` не нужны — только для
реального end-to-end прогона через `uvicorn`/`chat_cli.py`.

## Что реализовано (фаза 0 из ТЗ)

- [x] `LLMProvider`-адаптер: ядро не импортирует `anthropic` напрямую нигде,
      кроме `llm/claude.py`.
- [x] Device-agnostic `ToolRegistry`: цикл tool use работает и с нулём
      зарегистрированных тулов.
- [x] Персона: 3 режима, `adaptive` — реальный (пусть и эвристический v1)
      компонент, обновляющий сигналы стиля по `resident_id`, а не просто
      статический пресет.
- [x] Персистентная память (SQLite): настройки дома + предпочтения жильца.
- [x] `POST /chat` → `{response, actions}`, `GET/PUT /settings`.
- [x] Jarvis отвечает на общие вопросы без единого домашнего тула.

## Что сознательно отложено

Со ссылкой на [docs/TZ.md §8](docs/TZ.md) — следующие фазы:

- **Фаза 1–6: домены умного дома** (`light`/`switch` → `cover` → `climate` →
  медиа/техника → энергетика/участок → безопасность/доступ) — регистрируются
  в уже готовый `ToolRegistry` по одному, от простого к самому рискованному.
  Старый climate-прототип (`app/tools.py`, `app/prompts.py` — удалены этим
  коммитом) переезжает в фазу 3 в новом виде, поверх реестра.
- **Слой внимания** — стартует, когда доменов/датчиков достаточно, чтобы
  было что комментировать проактивно.
- **Голосовой пайплайн и iPad-приложение** — backend спроектирован как
  чёрный ящик `текст → {response, actions}`, к которому они подключатся
  без изменений в ядре.
- **Выбор финальной LLM** — открытый пункт ([docs/TZ.md §9](docs/TZ.md));
  Claude — дефолт на время разработки, смена не требует переписывания ядра.
