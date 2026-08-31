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
│   │     (llm/claude.py — дефолт; llm/ollama.py — self-hosted│
│   │      proof of concept, см. ниже)                        │
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
      claude.py                 # ClaudeProvider — дефолт, лучшее качество tool use
      ollama.py                  # OllamaProvider — free/self-hosted, CPU proof-of-concept
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

## Альтернатива Claude: локальный self-hosted провайдер (Ollama)

`LLM_PROVIDER=ollama` в `.env` переключает ядро на бесплатную, полностью
локальную модель через [Ollama](https://ollama.com) — без единого платного
API-вызова. Проверялось на этой машине (без GPU, только CPU) с
`qwen2.5:1.5b`:

```bash
# один раз: ollama pull qwen2.5:1.5b, дальше служба Ollama работает в фоне
```

```
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_BASE_URL=http://localhost:11434
```

**Честно про качество**: на общих вопросах работает нормально ("какая
столица Франции?" → "Paris."), но **на tool use ощутимо менее надёжна**, чем
Claude — в ручной проверке одна и та же команда ("включи свет в спальне") на
одном прогоне пропустила обязательный параметр `on`, на другом добавила
несуществующее поле `object`, которого нет в схеме тула. Это ожидаемо для
модели такого размера на CPU, не баг адаптера. Использовать это стоит как
доказательство, что ядро реально llm-agnostic (не привязано к Claude), а не
как замену для реальных сценариев управления домом — тем более что
tool-use-домены (фаза 1+) как раз про надёжное управление реальными
устройствами.

## Как гонять тесты

```bash
cd backend
pytest
```

Все тесты изолированы от внешнего мира — ни разу не идут в сеть и не трогают
рабочую БД:
- `test_llm_claude.py` — `ClaudeProvider` мокает HTTP-вызов `AsyncAnthropic`,
  проверяет только перевод схем/content-блоков в обе стороны.
- `test_llm_ollama.py` — `OllamaProvider` мокает HTTP-вызов к Ollama, включая
  перевод истории сообщений в OpenAI-стиль (tool_calls/role:"tool"), которого
  `ClaudeProvider` не требует, — реальный Ollama для CI не нужен.
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
- [x] Второй провайдер (`llm/ollama.py`, self-hosted/бесплатный) подключён
      без единого изменения в `agent.py` — подтверждает, что адаптер сделан
      правильно. Качество tool use на маленькой модели ниже, чем у Claude —
      см. раздел про Ollama выше.
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
