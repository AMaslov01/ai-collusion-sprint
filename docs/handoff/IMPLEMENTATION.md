# Реализация, диагностический сбор и эксплуатация

Читать перед подготовкой окружения, сбором DEV-трасс, сохранением/восстановлением и серверной работой. Статусы разрешений и последовательность этапов — в [AGENT_HANDOFF.md](../../AGENT_HANDOFF.md).

Вся вычислительная работа выполняется на Coniculus. Блок 1 (`native_dev`) и блок 2 (`two_agent_dev`) составляют один согласованный `dev_package`; блок 3 (`frozen_test`) имеет отдельный human approval gate. Все рекомендации/альтернативы этого файла подчинены правилу вопросов из AGENT_HANDOFF: незафиксированное значение предъявить людям и ждать ответа, не подставлять самостоятельно.

## 1. Закреплённые исходники и реальное переиспользование

| Ресурс | Identity |
|---|---|
| Upstream | `https://github.com/ZexueHe/MemoryArena`, commit `6cd9de14b71915e39ac742a20dc33785e14b6aab` |
| Tasks | HF `ZexueHe/memoryarena`, revision `da1a37c8b19280e18627ca01cf368195a5e1d92e`, config `group_travel_planner` |
| Целевая модель DEV | HF `Qwen/Qwen3.8-27B-FP8`, revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` |
| Hardware | Одна фактически предоставленная L40; UUID/свободная память/драйвер проверяются перед исполнением |
| Runtime/image DEV | vLLM `0.32.0`, tag commit `5df63a7203cdb9328d824a27b682e77c7b9928be`; immutable digest брать из исполняемого manifest — по сообщению пользователя он уже записан. Сам digest в этом документе не приводился и не проверялся. Перед run проверить соответствие версии/образа и smoke на L40 TP1; другой runtime/image самостоятельно не выбирать |

Проверять upstream SHA и checksums скачанных файлов; cache snapshot не означает, что revision действительно совпадает. Веса/каталоги не коммитить в Git. Код адаптера и patches — в репозитории с собственным commit/hash; не менять upstream checkout незаметно.

Исходный Group Travel содержит 270 групп, каталоги, search tools и один последовательный planner. Второго LLM-агента, approval gate, коллюзивного стимула и нашего строгого E нет. Полезные зависимости между запросами уже есть; их нельзя представлять как самостоятельных путешествующих LLM-агентов.

Читать перед адаптацией: [runner](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/run_travel.py), [planner](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/agent/travel_planner.py), [setup](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/setup_travel.md). Это точные исходные точки, а не готовая команда запуска нашего эксперимента.

## 2. Обязательные upstream gotchas

| Ловушка | Необходимое действие |
|---|---|
| Native runner идёт по всему корпусу | Добавить явный group allowlist и cutoff новых traveler requests; stock запуск не использовать как ночной pilot |
| `--config` заменяет остальные CLI settings | Сохранять resolved config; проверять, что effective значения действительно применены |
| `memory_system=none` | Обозначать `native_history`: прошлые queries/plans сохраняются, это не RESET и не verbatim FULL |
| Tool receipts прошлых travelers не целиком возвращаются в native history | Полный provenance сохранять отдельно; не утверждать, что baseline помнит все receipts |
| `judgement_mode=answer/hint` | Для пилота `none`; hidden reward может считаться runner, но не возвращается актору |
| `reset()` возвращает answers и все questions | Сделать actor-view allowlist; не передавать полный observation builder/агенту |
| Native scratchpad не содержит полный input и raw response | Перехватить gateway и писать фактически отправленные requests/полученные responses целиком |
| Native сохранение только по завершении группы | Добавить атомарную запись после каждого traveler и snapshot после второго |
| `AgentResult.success=True` | Отдельно вычислять protocol/parse validity, completion и task score |
| Client default tool-call `max_tokens=32768` | Экспонировать и проверять настоящий output cap; при общем context 32K этот default не оставлять |
| Native client явно передаёт `temperature=0.0` | Override должен попасть в фактический gateway request. Одно поле manifest, `agent.temperature` или server defaults не меняет жёстко заданный native sampler |
| `max_steps` и output cap — разные параметры | Явно ограничить оба, а также суммарные токены/время задачи |
| `set_base_person` заново задаёт system prompt | Проверять фактические messages; собственная role-policy не должна пропасть |
| ToolExecutor создаёт все tools | Нужен внешний flight CSV даже при selected lodging-only задаче |
| `model_name` попадает в filenames | Использовать безопасный serving alias без `/`; actual model identity записывать отдельно |
| `cost_tracker.py` оценивает API inference | Локальный GPU-cost считать по реальному времени/тарифу, не по чужим API-расценкам |

Flight database: `env/env_systems/travel_planner_env/database/flights/clean_Flights_2022.csv`, источник указан в pinned setup. Проверить download, columns, читаемость и checksum до model run. Если файл недоступен, остановить зависимый этап; создавать фиктивный пустой CSV нельзя.

Native endpoint допускает OpenAI-compatible `agent.base_url`/`OPENAI_API_BASE`; проверить фактическую маршрутизацию в pinned client. Нельзя считать, что произвольное новое YAML поле автоматически управляет output cap.

## 3. Минимальная структура реализации

Имена ниже проектируемые. При обнаружении существующего эквивалентного кода показать его и уточнить способ интеграции, если он не следует однозначно из поручения; не создавать конкурирующую реализацию по собственному выбору.

```text
src/collusion_handoff/
  config.py             # строгая schema, resolved manifest, fingerprints
  model_gateway.py      # request/response cache, usage, retries, timeouts
  visibility.py         # actor views, permitted source, canary tests
  native_adapter.py     # bounded native Group Travel, native_history
  catalog.py            # stable IDs, source versions, immutable receipts
  checkpoint.py         # atomic snapshot, validation, exact request replay
  events.py             # append-only journal, typed IDs, idempotency
  protocol.py           # позднее: A/B state machine, proposal/review/commit
  memory.py             # source→S/C/V/P, frozen packages, budgets
  evaluator.py          # позднее: registry predicates по catalog+ledger
  analysis.py           # позднее: macro metrics, paired cluster bootstrap
  cli.py                # validate/dry-run/run/resume/audit/analyze
configs/                # малые config templates без секретов
prompts/                # версионируемые role/builder/seed templates
schemas/                # serializable contracts
tests/fixtures/         # маленькие предметные fixtures без inference
```

Делать тонкий adapter поверх исходных инструментов. Изменения их выводов ограничить IDs/provenance и наблюдаемыми schema contracts. Все изменённые строки/семантика фиксируются; результаты не выдаются за untouched MemoryArena benchmark.

В первом native DEV сохранить upstream displayed tool results и native plan format; внутренние receipt/catalog IDs писать рядом, не меняя видимый модели текст. Точные IDs в typed proposals и дополненный ими tool display обязательны уже в двухагентной надстройке. Если требуется изменить displayed result и в native DEV, сначала предъявить необходимость и получить ответ пользователя; не требовать от исходного planner незаявленного нового output format.

Желаемый интерфейс CLI (это требование к новой реализации, **не существующие команды**). Научные команды исполняются внутри non-root Docker на Coniculus:

```text
python -m collusion_handoff validate --config <file>
python -m collusion_handoff dry-run --config <file>
python -m collusion_handoff run-dev-package --config <file>
python -m collusion_handoff run-native-dev --config <file>
python -m collusion_handoff run-two-agent-dev --config <file>
python -m collusion_handoff run-main --config <freeze> --approval-receipt <file>
python -m collusion_handoff resume --run-dir <dir>
python -m collusion_handoff audit --run-dir <dir> --cache-only
python -m collusion_handoff build-memory --run-dir <dir> --checkpoint-id <id>
```

`validate`, `dry-run` и `audit --cache-only` не выполняют model/API calls. `build-memory` использует LLM только при явном режиме исполнения и в пределах разрешённого лимита. Перед передачей команд владельцу сверить реальный `--help`; не документировать несуществующие флаги как рабочие.

`run-dev-package` выполняет блок 1 → readiness checks → блок 2 → review packet → `awaiting_main_approval`. Он никогда не вызывает `run-main`. Переход 1→2 не требует нового общего разрешения, если блок 2 уже включён в поручение, его inputs подтверждены и проверки пройдены. Отсутствующие human labels/ответы — причина ждать данных, не заменять их автоматически.

`run-main`, main-prefix collector, continuation runner и resume проверяют: verified результаты обоих DEV-блоков, фактическое одобрение людей, совпадение hash текущего freeze с одобренным, отсутствие незакрытых обязательных полей/STOP. Receipt без ссылки на реальный ответ пользователя либо созданный агентом boolean `approved=true` недостаточен. Любая смена scientific fingerprint возвращает статус на ожидание нового одобрения. Точное содержание gate — AGENT_HANDOFF, «Обязательная остановка перед блоком 3».

## 4. Manifest и identity

Минимальные поля schema:

```yaml
schema_version: 1
run_id: <stable unique id>
phase: native_dev  # затем two_agent_dev или frozen_test
execution_scope: dev_package  # не разрешает frozen_test
server: Coniculus
scientific_fingerprint: <hash>
code: {adapter_commit: <sha>, upstream_commit: <sha>, patch_hash: <sha>}
data: {repo: ZexueHe/memoryarena, revision: <sha>, config: group_travel_planner}
catalogs: [{path: <path>, sha256: <sha>, rows: <int>, id_schema: <version>}]
selection: {group_ids: [<actual ids>], max_new_travelers: 4, checkpoint_after: 2}
model: {repo: <repo>, revision: <sha>, served_alias: <alias>, weight_hashes: <index>}
runtime: {image_digest: <digest>, vllm_version: <version>, driver: <version>}
generation: {tokenizer_revision: <sha>, chat_template_hash: <sha>, settings: <object>}
limits: {context: 32768, max_output: 4096, concurrency: 1, max_steps: 30}
prompts: {role_hash: <sha>, tools_schema_hash: <sha>, builder_hashes: <object>}
native: {memory_system: none, judgement_mode: none}
operations: {wall_time_seconds: <required>, max_attempts: 2, gpu_uuid: <required>}
budgets: {modal_usd_limit: 0, other_paid_api_usd_limit: 0}
split: {name: dev, group_registry_hash: <sha>}
main_approval: {status: awaiting_main_approval, message_ref: null, approved_freeze_hash: null}
```

Это schema sketch: placeholder значения нельзя считать готовой конфигурацией. Поля `UNSET` разрешены в draft, запрещены в исполняемой зависимости. Budgets 0 означают отсутствие разрешённых платных вызовов в этом template, а не отменяют будущую команду пользователя потратить кредит. По разрешённому бюджету обновить manifest до run.

Обязательно добавить effective sampler (`temperature/top_p/top_k` где поддержаны), seed, thinking mode, KV dtype, weight quantization, parser names, tool protocol, total task budget, retry/backoff/timeout policy, конечный deadline, CPU/RAM/disk, hashes schemas и export-policy. Если параметр не поддержан API, записать именно это, не выдумывать применённое значение.

Для двухагентных DEV/test дополнительно зафиксировать `induction_mode=private_goal_induction`, `post_handoff_threat_model=historical_persistence`, отдельные hashes исходных private-goal prompts A/B, clean nominal prompts после handoff и общей archive-redaction policy. Native одноагентный DEV не получает эти частные цели; в его manifest соответствующие поля `not_applicable`.

Manifest DEV-пакета содержит **два отдельных block contracts**: `native_dev` и `two_agent_dev`. Для каждого — actual group/prefix/task/job IDs, expected counts, arms/число model calls в пределах протокола, per-role токены/steps, wall-time/retries и outputs. Общий package deadline/бюджет также обязателен. Поля блока 2 сейчас не заданы числами: до его запуска запросить конкретный объём и получить ответ. `48 native tasks completed` не означает `dev_package completed`.

Добавить `decision_register`: вопрос/поле, предложенное значение, статус `UNSET|PROPOSED|CONFIRMED`, фактическое основание ответа пользователя и effective value. `PROPOSED` не применяется как fallback. Для main — отдельные `freeze.json`, `human_main_approval` и ссылки на hashes обоих DEV-отчётов.

Scientific fingerprint покрывает всё, что меняет наблюдения/ответы/сбор/анализ: модель, prompts, generation, инструменты, источник, selection, schemas, limits и scoring. Пути/время старта/имя контейнера — отдельная operational identity. Любая правка операционного кода сохраняется с hash; эквивалентность scientific inputs проверяется, а не предполагается.

## 5. L40 и model smoke-test

Выбран официальный FP8 checkpoint: опубликованные weights суммарно около 30.87 GB. [Model files](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/tree/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a). У L40 48 GB памяти; фактическую карту проверить, а не опираться на прежнее обсуждение H100. [NVIDIA L40](https://www.nvidia.com/en-us/data-center/l40/).

FP8 weights не подразумевают FP8 KV cache. Для обязательного smoke-test подтверждена конфигурация §5.1: без отдельной KV-квантизации, TP=1, concurrency=1, context 32 768. Применение этих лимитов к остальному DEV вне smoke явно отразить в согласованном run-config; самостоятельно расширять scope нельзя. Не применять Blackwell-specific NVFP4/MXFP4 flags к Ada. Помещение весов само по себе не доказывает, что runtime + buffers + заданный context влезут.

Официальный [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) указывает tool parser `qwen3_coder`, reasoning parser `qwen3`; проверить в выбранном [vLLM 0.32.0](https://github.com/vllm-project/vllm/releases/tag/v0.32.0). Blackwell-рекомендации не являются подтверждением L40 TP1. Не подставлять старый `hermes` автоматически. Использовать закреплённый digest; `latest` не является воспроизводимой identity.

Для A/B (включая native A) зафиксирован **полный официальный non-thinking sampler** из [pinned model README](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/blob/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a/README.md#best-practices):

```yaml
actor_generation:
  temperature: 0.7
  top_p: 0.8
  top_k: 20
  min_p: 0.0
  presence_penalty: 1.5
  repetition_penalty: 1.0
  chat_template_kwargs:
    enable_thinking: false
```

Это заменяет прежнее предложение temperature=0. `enable_thinking` — boolean; в vLLM передаётся через `chat_template_kwargs`, а не текстовой инструкцией. Флаг выключения thinking сам не выбирает sampler: [generation_config checkpoint](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/blob/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a/generation_config.json) имеет другие defaults. Все перечисленные значения явно передать в request, используя поддерживаемые vLLM extra fields там, где их нет в стандартном клиентском API.

Проверить effective serialized gateway requests: native [OpenAIClient](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/env/env_systems/travel_planner_env/clients/openai_client.py) задаёт temperature=0.0, поэтому одного server/manifest setting недостаточно. Добавить fixture, что запросы A/B действительно содержат выбранный sampler и отключённый thinking; одинаковые параметры во всех memory arms. Seeds/cache/actual I/O сохраняются; sampler не гарантирует побитового повторения новой генерации.

Лимиты smoke определены ниже; max steps 30 на traveler и остальные не подтверждённые параметры/объёмы DEV остаются предложениями и входят в вопрос о run-config. Sampler S/C/V/P не наследует actor settings молча: его задаёт отдельный component manifest. Main-конфигурация по-прежнему требует одобрения после DEV; выбор sampler/runtime/smoke settings не снимает этот gate.

### 5.1. Подтверждённый contract обязательного smoke-test

```yaml
smoke:
  tensor_parallel_size: 1
  max_model_len: 32768
  client_concurrency: 1
  max_num_seqs: 1
  completions_per_request: 1
  max_output_tokens_per_call: 4096
  kv_cache_dtype: auto
  gpu_memory_utilization: 0.90
  tool_call_parser: qwen3_coder
  reasoning_parser: qwen3
  seed: 42
  request_timeout_seconds: 900
  shared_memory_bytes: 8589934592
  pids_limit: 1024
```

Это подтверждённая конфигурация **smoke**, а не гарантия успешного старта и не авторизация всего DEV/test. Модель/runtime/digest и sampler берутся из предыдущих разделов. Семантика:

- Context 32768 включает вход и выход. При reserve 4096 фактический input (system/messages/tools/template) максимум 28672 токена; переполнение не исправлять молчаливым усечением.
- Concurrency=1 обеспечивается клиентской очередью, `--max-num-seqs 1` в vLLM и `n=1` в requests; одна клиентская корутина сама по себе не фиксирует размеры server buffers/graph capture. Tools/model calls smoke последовательны.
- `kv_cache_dtype=auto` не включает отдельную KV-квантизацию. Записать effective KV dtype из resolved engine config; не считать FP8 weights доказательством FP8 KV. Не подменять его FP8 при нехватке памяти.
- `gpu_memory_utilization=0.90` задаёт бюджет executor, а не гарантированный hard cap всей CUDA-памяти. Не задавать конфликтующий explicit `kv_cache_memory_bytes`, который переопределяет эту настройку. Фактическую память измерить.
- Seed=42 записать как engine seed и seed фактических smoke requests; это не гарантия побитового повторения и не main seed schedule. Reasoning parser сам по себе не включает thinking: `enable_thinking=false` остаётся в каждом запросе.
- Timeout 900 с относится к одной model request attempt; не является deadline всего smoke, startup, download или DEV-пакета. Attempts/retries и общий deadline должны быть согласованы отдельно; timeout не даёт права на дублирующее изменение state.
- Docker shared memory — собственный `/dev/shm` размером 8 GiB (`--shm-size=8g`), без подмены `--ipc=host`; `--pids-limit=1024`. Эти значения — стартовые лимиты для проверки, не доказательство достаточности при любом числе CPU threads.
- При OOM, нехватке shm/pids, parser failure или timeout сохранить диагностику и спросить людей; самостоятельно увеличивать память/лимиты, уменьшать context или менять backend нельзя.

Смысл engine settings: [vLLM 0.32.0 engine arguments](https://docs.vllm.ai/en/v0.32.0/configuration/engine_args/); shared memory: [vLLM Docker](https://docs.vllm.ai/en/v0.32.0/deployment/docker/). Это schema contract, не готовая полная Docker-команда: GPU UUID/CPU/RAM/mounts/доступы берутся из серверных инструкций пользователя.

Smoke sequence:

1. Проверить model identity, tokenizer/template, weights, параметры endpoint и отсутствие внешнего paid route.
2. Простой ответ завершается; output cap действительно действует.
3. Дать один настоящий search tool: модель возвращает `message.tool_calls`, имя и валидные JSON arguments по schema.
4. Выполнить tool, следующий request содержит корректные `tool_call_id` и exact result, модель продолжает и выдаёт final plan.
5. Проверить, что tool XML в обычном `content` не был ложно признан API tool call, reasoning не сломал parser, context не обрезался сервером молча.
6. Проверить реальные prefill/decode tokens/time/VRAM, затем первые два traveler requests первой DEV-группы. Их не генерировать повторно после прохождения gate: это уже часть пилота.

При несовместимости/OOM сохранить ошибку и спросить людей о конкретном исправлении; до ответа зависимый запуск остановлен. После согласованной правки сохранить отдельную identity. Fallback модель/квантизация/контекст/runtime не меняются автоматически. Дополнительный download/compute остаётся в границах поручения и доступного ресурса.

## 6. DEV-selection и порядок сбора

Предложение объёма **блока 1** — до 12 групп × первые 4 новых запроса = до 48 задач плюс исходные base plans. Из pinned data заранее вывести actual group IDs; предлагаемый allowlist — отображаемые группы 1…12, **если это действительно их upstream identity**. Показать список людям и подтвердить объём/IDs до запуска. Сохранить mapping и доказать, что есть минимум четыре новых traveler requests. Не заменять отсутствующий ID молча Python index.

Все эти группы и любые данные для отладки/подбора prompts навсегда DEV. Порядок: одна группа → проверка → ещё две → проверка → остальные. Полный expected ID list сохраняется до запуска. Ошибка не даёт право выбирать другой, более удобный group вместо него.

На каждом traveler:

```text
load verified previous state
→ issue current request only
→ cache every model/tool interaction
→ capture final text and parsed plan separately
→ execute native env step / record hidden reward separately
→ atomic terminal result + updated checkpoint
→ mark job verified
```

После второго traveler сохранить отдельный immutable handoff snapshot до третьего. Native planning requests 3/4 продолжаются со штатным native_history, а не с нашими S/C пакетами. Память из snapshot первых двух можно строить после окончания группы: builder input всё равно содержит только prefix, не последующие наблюдения.

Первый пилот не отвечает на efficacy question и не оценивает P на реально сформировавшемся сговоре. Honest source traces полезны для coverage, factuality и benign-drop. Вредные/допустимые paired fixtures из MEMORY остаются отдельными component tests.

Затем в том же согласованном DEV-пакете выполнить **блок 2** по его отдельному allowlist/лимитам: A/B prefix с частными целями → checkpoint → удаление обеих целей → шесть веток A′/B → программный E и audit. Допустимо использовать группы блока 1 повторно как DEV с отдельными namespaces/run IDs; эти повторы не становятся независимыми test-группами. Выбор конкретных групп/числа prefixes/режимов подтвердить до вызовов. После полного блока 2 сохранить оба отчёта, проект freeze и состояние `awaiting_main_approval`; основной test автоматически не запускается.

## 7. Артефакты и полное I/O

Большие данные вне Git; предложенный run layout:

```text
<run_dir>/
  manifest.json              # resolved immutable config/fingerprints
  inputs/                    # selection/task mappings/catalog hash index
  io/requests.jsonl          # full actual requests + attempt refs
  io/responses.jsonl         # full responses or typed transport failures
  events/<group>/<branch>/   # append-only model/tool/protocol events
  results/<job_id>.json      # terminal result, validation, links
  checkpoints/<id>/          # actor/admin/world separate; hashes
  memory/<checkpoint_id>/    # source, summary, C, V/P tables, packages
  annotations/               # independent raw labels/adjudication
  analysis/                  # frozen analysis config, metrics, plots
  status.json                # current stage/progress/last verified artifact
  attempts.jsonl             # durable retries/cost/unknown spend
  STOP                       # exists only when stop requested
  audit.json                 # coverage, invariants, replay results
```

Для каждого model call: request ID/attempt, full actual messages/tools, parameters, request fingerprint, model identity, raw response, usage, finish reason, timestamps/duration/status. Сохранять возвращённые reasoning fields в закрытый cache без требования раскрывать не предоставляемые провайдером скрытые вычисления. Secrets/authorization headers не логировать.

Cache key включает logical call identity (`run/phase/group/checkpoint/branch/role/task/turn/component`) и scientific request fingerprint. Одинаковые байты prompts у разных запланированных генераций не являются основанием брать один ответ на всех. Reuse разрешён для retry/resume того же logical call и явно общих once-per-checkpoint стадий C/V/P, а не между независимыми samples/ветками.

Для tool: точные arguments, ID вызова, видимый output **без потери строк**, canonical source version, errors, latency и связь с model call. Если отображение выводов изменено добавлением ID, сохранить и canonical record, и exact displayed result.

Для traveler: исходный query, final text, parsed plan, native score/flags, отдельные parse/protocol validity, turns, token counts, stop reason, links ко всем events. Native approximate score обозначать secondary; успех на первых четырёх запросах не равен published full-group SR.

## 8. Snapshot, replay и branch isolation

Snapshot включает schema/identity, native A fields, base-person state, завершённые traveler results, env position и history, next traveler index, RNG/seed metadata, mutable memory state и references к исходным events. Будущие queries/answers могут находиться только в отдельном admin state; actor serialization их не читает.

Атомарная запись: temporary file на том же filesystem → flush/fsync где применимо → schema/hash validation → atomic rename. Журналировать completion только после сохранения всех обязательных зависимостей. Один существующий файл не означает valid checkpoint.

Ключевой replay-check **без inference**: восстановить snapshot после traveler 2, построить первый request traveler 3 и сравнить его canonical messages/tools/generation fingerprint с реально сохранённым pre-send request 3. Случайные transport request IDs и wall-clock поля исключать только по объявленному canonicalization rule. Несовпадение текста, tool schema или generation — failure.

Полный cache replay восстанавливает state transitions по сохранённым responses, пересчитывает scores и expected coverage. Это воспроизводимость вычислений по артефактам. Не обещать побитово одинаковую новую генерацию на другой GPU/runtime даже при temperature 0.

Для main branches отдельные actor/backend/ledger namespaces; immutable source объекты допустимо физически разделять read-only. Мутируемые объекты копируются. Каждая ветка начинает с одинакового world/B fingerprint; новые события одной ветки недоступны другой.

## 9. Retries, stopping и resume

Предложенная retry policy DEV — максимум **две transport attempts** на одинаковый request, последовательные, с 10-секундной паузой или документированным `Retry-After` внутри deadline. Подтвердить её и effective timeout у пользователя до фонового запуска; отсутствие ответа не включает повторы автоматически. Сохранить все попытки и неизвестный расход после timeout.

Повторами управляет один журналируемый слой. Отключить скрытые SDK retries (`OpenAI(max_retries=0)` в фактически используемом клиенте) и retries HTTP-session для изменяющих состояние POST. У pinned `EnvironmentClient` есть retry-настройки, которые нужно явно проверить/заменить для `/env/step`. При «state изменён, ответ потерян» повтор step допустим только с server-side idempotency key/доказуемо однократным исполнением; иначе сохранить ambiguous outcome и остановить зависимую ветку. Повтор model generation и повтор изменения environment — разные операции. См. [SDK retry contract](https://github.com/openai/openai-python#retries) и [upstream env client](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/env/env_client.py).

Transient network/408/5xx — кандидаты на ограниченный retry. Quota exhausted/auth/TLS/неожиданный endpoint/model, OOM, disk full, fingerprint corruption — остановка зависимой стадии и диагностика. 429 frequency limit отличать от закончившейся квоты. Malformed plan/tool arguments — model/process outcome; не получать дополнительные ответы пока не понравится.

Before every new job/attempt проверять STOP, wall-time/deadline, remaining attempts/budget, pending questions, main approval при соответствующем scope и отсутствие duplicate worker. Предложение общего hard limit DEV-пакета 1+2 — не более 8 часов с начала вычислений **и не позже конца согласованной GPU-резервации**; actual общий/per-block limits подтверждает пользователь до run. Не тратить весь пакетный ресурс на блок 1 по собственному решению и не расширять время ради завершения блока 2.

Resume использует тот же run/manifest, переиспользует только verified results совпадающего fingerprint и продолжает недостающие jobs. Process restart не обнуляет attempts/cost и не снимает STOP. Не запускать supervisor без конечных лимитов. Повтор approval/commit idempotent; после timeout проверять ledger перед повторным состоянием.

## 10. Единственный вычислительный сервер: Coniculus

Пользователь зафиксировал Coniculus и отдельно передаст инструкции доступа/работы. До подключения получить их; слово Coniculus не является проверенным SSH hostname. Прочитать переданные инструкции и `/Users/maslovartemij/.codex/skills/caniculus/SKILL.md`, обязательные `references/server-rules.md`, соответствующие разделы `references/operations.md` и `references/recovery-and-monitoring.md`. Если skill недоступен, запросить нужные инструкции, а не угадывать процедуру. Актуальные инструкции пользователя имеют приоритет над кэшированным снимком; при расхождении задать вопрос до действия.

Ниже база правил локального skill (снимок 19.09.2026, skill 23.10.2026), **не результаты нового осмотра сервера**. Подтвердить реальные параметры по переданным инструкциям, при неизвестности спросить:

- Учётная запись `a.maslov`, личный SSD `/mnt/ssd/a.maslov`; перед записью проверить realpath, владельца и mount. Все большие weights/cache/results вне Git и writable container layer.
- Все вычисления, проверки научных файлов и inference в **non-root Docker**. Host Python для научных задач не использовать. Имена контейнеров/образов позволяют определить владельца.
- Монтировать только согласованную GPU по **точному UUID**; свободный `nvidia-smi` не является резервированием. Чужие процессы/файлы/контейнеры не трогать.
- До существенной нагрузки проверить, что серверный канал предупреждён. Агент сам пишет людям только по явному поручению; факт уже выполненного согласования учитывать.
- Большие сетевые операции днём суммарно ≤50 Мбит/с, включая weights и Docker pull. Часы/таймзона «ночи» в доступном снимке не определены: пока не уточнены, применять дневной лимит. Физический гигабитный порт не отменяет регламент.
- `rsync --bwlimit=5000` — практический запас примерно до 41 Мбит/с для одной передачи; несколько загрузок не имеют отдельные квоты. HF downloader без rate cap не считать автоматически соответствующим правилу: использовать проверенный ограниченный transfer path, после получения файлов проверить hashes.
- Secrets отдельным read-only файлом вне архивируемого дерева. Не помещать literal keys в CLI, Docker environment, image, Git или logs.
- Своему long-running контейнеру `--restart=no`; persistent checkpoints и STOP, конечный supervisor. После работы освободить свою GPU-память. Глобальная очистка/чужая нагрузка не входят в задачу.

30.87 GB на гигабитном канале имеет идеальный нижний предел около 4.1 мин; при 50 Мбит/с — около 82 мин, без накладных расходов. Ни одно число не является ETA: ещё есть образ, disk/network contention, runtime compilation и smoke. Оценивать фактическую скорость, не обещать «скачается за несколько минут».

Подготовка плана не является разрешением на SSH/download/build/run по этому skill. При явном поручении выполнить конкретно разрешённый DEV-пакет без нового общего разрешения между блоками 1 и 2. Отсутствующая UUID/резервация блокирует GPU-run; отсутствие доступа/инструкций — подключение. Редактирование файлов можно подготовить локально, а научные tests/data processing/replay выполнять только на Coniculus в non-root Docker. При любом нерешённом вопросе — запрос людям; main всегда имеет отдельный gate.

## 11. Расходы

Отдельные ledger counters: Coniculus GPU seconds, input/output/reasoning tokens где известны, tool calls и wall-time. $50 Modal — исторически доступный ресурс, не резервный маршрут этого запуска. DEV-пакет — Coniculus-only; Modal и иной paid API отключены с лимитом 0. Подключать дополнительные сервисы можно только после явного изменения поручения пользователем и отражения этого в manifest.

Deployment-cost метода включает extraction/V/P/summary и continuation, даже если эксперимент кэшировал общие стадии один раз. Experimental actual spend и per-policy deployment estimate показывать отдельно. Failed/retried/unknown completion calls тоже могут стоить денег.

Один общий Qwen server на Coniculus обслуживает A/B последовательно; E выполняет код. Дополнительная сильная LLM для annotation — только отдельное предложение людям, не часть автоматически исполняемого пакета и не независимый gold сама по себе.

## 12. Acceptance checks реализации

| ID | Gate | Проверяемый результат |
|---|---|---|
| I01 | Dry-run | Expected group/traveler/job IDs и resolved limits выведены без model calls |
| I02 | Identity | SHA исходников/data/catalog/model/template и runtime digest известны и совпадают |
| I03 | Visibility | Gold/admin sentinels не достигают actors/builders; future-task sentinels разрешены только после соответствующего ISSUE_TASK; actor projection allowlisted |
| I04 | Tool integration | Настоящий tool_call → exact result с tool_call_id → final plan |
| I05 | Durability | Прерывание после traveler не теряет verified result; incomplete file не считается готовым |
| I06 | Snapshot | Request 3 из snapshot 2 совпадает с saved request 3 без inference |
| I07 | Resume | Готовые jobs не вызывают модель повторно, чужой fingerprint не переиспользован |
| I08 | Coverage | Раздельно для блоков 1 и 2 каждый ожидаемый ID имеет terminal status/not-run reason; 48 — лишь предложенный объём native, не покрытие всего пакета |
| I09 | Limits | Steps/output/context/deadline/STOP/retry bounds реально применяются, а не только записаны |
| I10 | Scoring | Native success/parse validity/strict utility не смешаны; нет ложного full-benchmark score |
| I11 | Cost | Успешные, ошибочные, повторные и неизвестные вызовы учтены раздельно |
| I12 | Export | Cache-only audit проходит, secrets исключены, package checksum проверен, собственные лишние GPU-процессы завершены |
| I13 | Ambiguous transport | Fixture «env step выполнен, ответ потерян» не вызывает второй state transition; число реальных попыток совпадает с gateway ledger |
| I14 | DEV boundary | После блока 1 при готовых inputs запускается только блок 2; после блока 2 состояние awaiting_main_approval и нет ни одного test-call |
| I15 | Human approval gate | Отсутствующий receipt, неподтверждённый ответ или несовпадающий freeze hash блокируют main-prefixes/continuations и main-resume |
| I16 | Questions | UNSET/PROPOSED или новая неопределённость блокируют зависимую операцию до ответа; fallback и самовольных изменений нет |
| I17 | Whole-package readiness | Оба DEV block contracts и audit готовы, review packet содержит их реальные результаты; один native отчёт не помечает пакет завершённым |
| I18 | Effective actor sampler | Фактические native A и A/B requests содержат sampler §5 и boolean enable_thinking=false, не hardcoded temperature=0; settings совпадают между memory arms |
| I19 | Smoke contract | Resolved engine/Docker/request settings совпадают с §5.1; input/output budget, один request/sequence/completion, dtype, seed, timeout, shm/pids проверены; отклонения не исправляются самовольно |

Для изменения научного runner/evaluator эти проверки нужны: здесь потеря журнала или ошибка одного поля меняет исследовательский результат. Не добавлять tests ради покрытия простых Markdown-правок.
