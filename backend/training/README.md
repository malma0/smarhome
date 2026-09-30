# Своя модель для дома

Цель: маленькая модель, дообученная **только на управлении домом**, которая
работает на своём компьютере (ПК с RTX 2060, 6 ГБ) — без лимитов, бесплатно и
без интернета. Облачные модели Groq ограничены 200 000 токенов в день на
модель; команды дому — узкая задача, и маленькая модель, выученная именно на
ней, может делать её не хуже большой.

База — **Qwen2.5-1.5B-Instruct**: лицензия Apache 2.0 (коммерческое
использование разрешено; у 3B лицензия другая — её обходим), хорошо знает
русский, помещается в 6 ГБ. Если окажется мало — Qwen2.5-7B-Instruct
(тоже Apache 2.0) в 4 бита, на 6 ГБ впритык.

## Как устроены данные

- `sim_house.py` — **случайный дом на каждый пример**: 3–7 комнат из 12, свои
  устройства, показания, нормы, краны, сценарии. Отвечают **настоящие**
  инструменты Jarvis (`app/domains/home.py`) — форма результатов и ошибок та
  же, что в жизни. На одном доме модель выучила бы «в спальне 22°», а не
  читать ответ дома.
- `intents.py` — «замысел» команды **и правильные вызовы, посчитанные
  кодом**: одна комната, несколько комнат (по вызову на комнату), весь дом,
  несколько устройств, вопросы о доме, нормы («держи 21», «потеплее» —
  сначала посмотреть норму, потом +1), жалобы («душно», «холодно»),
  сценарии, краны, просто разговор. Правильные ответы у больших моделей не
  берутся: на «выключи свет в спальне и на кухне» gpt-oss-120b и gpt-oss-20b
  выключили только спальню, qwen3.8-27b — весь дом.
- `teacher.py` — большая модель (Groq, запасные модели, чтобы не тратить
  лимит основной) **только пишет, как это сказал бы человек**. Всё кэшируется
  на диск — повторный запуск не тратит токены.
- `build_dataset.py` — отсеивает фразы, где не названа комната или число,
  **или сказано обратное** («включи…» при замысле «выключить» — такие были,
  16% в пилоте); выполняет вызовы на доме; ответ Jarvis строит
  `replies.py` — **по шаблонам**, с правильными родами и падежами, а ответы
  на вопросы о доме вычисляются по данным. Ответы учителя были
  неграмотные («Свет выключена», «включена в Прихожая, Зал»).
- Формат — диалоги с вызовами инструментов, как их рисует шаблон чата
  Qwen2.5 (и потом Ollama при запуске модели).

```bash
python -m training.build_dataset --intents 800 --phrasings 4 --out training/data/train.jsonl
python -m training.build_dataset --intents 150 --phrasings 1 --seed 777 --eval --out training/data/eval.jsonl
# виды, добавленные позже, - отдельными файлами, чтобы основной набор (и кэш учителя) не менялся:
python -m training.build_dataset --kinds brightness --intents 150 --phrasings 4 --seed 11 --out training/data/train_brightness.jsonl
python -m training.build_dataset --kinds brightness --intents 30 --phrasings 1 --seed 778 --eval --out training/data/eval_brightness.jsonl
python -m training.build_dataset --kinds curtains,humidifier,security,house_status,history,schedule --intents 360 --phrasings 4 --seed 21 --out training/data/train_house2.jsonl
python -m training.build_dataset --kinds curtains,humidifier,security,house_status,history,schedule --intents 60 --phrasings 1 --seed 779 --eval --out training/data/eval_house2.jsonl
```

Описания инструментов, на которых собраны данные, сборка кладёт в
`training/data/tools.json`. Модель, обученная на них, должна получать ровно их:
в экзамене — `--tools training/data/tools.json`, в Jarvis — скопировать этот
файл в `app/llm/home_model_tools.json`, когда модель подключается.

## Экзамен

`evaluate.py` — одинаковые задания любой модели: облачной (`groq:<модель>`)
или своей (`ollama:<модель>`). Каждое задание — свой дом и фраза; модель
работает через настоящего агента Jarvis, а оценивается **что изменилось в
доме** — набор изменений должен совпасть с правильным. Вопрос о доме должен
быть посмотрен, разговор — остаться разговором.

```bash
python -m training.evaluate --model groq:openai/gpt-oss-20b
python -m training.evaluate --compare
```

## Обучение на ПК с видеокартой

1. Код: `git pull` (или `git clone`), дальше всё в `backend/`.
2. Окружение: Python 3.11, `python -m venv .venv`, затем
   ```bash
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124
   .venv\Scripts\pip install -r training/requirements-train.txt
   ```
3. Проверка видеокарты:
   `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
4. Пробный прогон (минуты): `python -m training.train_lora --data training/data/pilot.jsonl --out training/runs/smoke --max-steps 20`
5. Обучение: `python -m training.train_lora --data training/data/train.jsonl training/data/train_brightness.jsonl training/data/train_house2.jsonl --out training/runs/home-1.5b`
   (LoRA поверх 4-битной модели; float16 — у RTX 2060 нет bfloat16).
6. Слить адаптер с базой: `python -m training.merge_adapter --adapter training/runs/home-1.5b/adapter --out training/runs/home-1.5b/merged`
7. В GGUF для Ollama — скриптом `convert_hf_to_gguf.py` из llama.cpp:
   `python convert_hf_to_gguf.py training/runs/home-1.5b/merged --outfile home-1.5b.gguf --outtype q8_0`
8. В Ollama: Modelfile с `FROM ./home-1.5b.gguf` и блоком `TEMPLATE` из
   `ollama show qwen2.5:1.5b --modelfile` (шаблон Qwen2.5 с инструментами),
   затем `ollama create jarvis-home -f Modelfile`.
9. Экзамен: сначала база без обучения — `ollama pull qwen2.5:1.5b`,
   `python -m training.evaluate --model ollama:qwen2.5:1.5b`, потом своя —
   `python -m training.evaluate --model ollama:jarvis-home`, и
   `python -m training.evaluate --compare`.

Модели и промежуточные файлы (`training/runs/`) в git не попадают; данные
(`training/data/`) и результаты экзаменов (`training/results/`) — попадают.

## Подключение к Jarvis

10. На ПК с моделью открыть Ollama в домашнюю сеть: переменная окружения
    `OLLAMA_HOST=0.0.0.0`, перезапуск Ollama, входящий TCP 11434 в брандмауэре
    **только для частной сети** (у Ollama нет пароля).
11. На компьютере с Jarvis в `.env`: `HOME_LLM_URL=http://<IP того ПК>:11434` и
    `HOME_LLM_MODEL=jarvis-home-v3`, перезапустить Jarvis. Запросы только про дом
    (`app/tools/router.py`) идут своей модели — промпт собирается тем же шаблоном,
    что при обучении (`app/llm/qwen_template.py`); остальное и дом, когда тот ПК
    выключен, — основной модели. В окне Jarvis появится «(ответила своя модель)».
