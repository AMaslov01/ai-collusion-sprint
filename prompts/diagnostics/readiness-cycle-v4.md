# v4: исправления и готовность полного эксперимента

Статус: ограниченный DEV; модельные вызовы открываются только после заполненного manifest и consistency review. Предыдущий v3 завершён и остаётся неизменным.

## Поручение и роли

Основание — прямое поручение владельца исправить проблемы совместно с другими чатами, добиться готовности полного эксперимента к вечеру и соблюдать контракт. Human message `01a16e81-449a-7152-a8f6-77d8a0a37419`, turn `01a16e81-4469-7a21-854f-cea4f75c427d`; сообщение независимо проверили исполнитель и диагностика.

- Дизайн/координатор: `01a1688e-8fee-7140-b505-6f93e571c740`. Владеет этим заданием и научным review.
- Исполнитель: `01a16af0-f00f-7a11-ae68-855fbf5f2e90`. Единственный владелец production code, runtime, тестов и серверного запуска.
- Диагностика: `01a16da8-0275-76e0-8936-770c4df31616`. Владеет отдельным `results/memory-group1-cp2/diagnostics-v4/`, независимыми expected fixtures, source-first references и аудитом. Production code не изменяет.

Обычные инженерные решения и конкретизацию малого DEV решать между этими чатами, документируя основание. Неразрешённый научный конфликт передать в дизайн. Это поручение не разрешает запуск main или подмену человеческого gold. Старые правила «каждый технический вопрос человеку» заменены последующими прямыми разрешениями владельца; main gate сохраняется.

Читать существующие `AGENT_HANDOFF.md`, `docs/handoff/{PROTOCOL,MEMORY,EVALUATION,IMPLEMENTATION}.md` по соответствующему действию. v4 уточняет только перечисленные здесь DEV-изменения. D09/D10, определение H/U/Q и источник истины E сохраняются.

## 1. Подготовить проверяемое исправление

1. Сохранить frozen v3 outputs/references/receipts. Группы 1–3 использовать только как developmental regression.
2. Подготовить native schema-constrained generation S/C/V/P на закреплённом vLLM; сверить фактические возможности image и первичную документацию. Схемы не подсказывают правильный verdict. Ошибки формата и незавершённые ответы остаются failures; дополнительных repair calls нет.
3. Сначала уменьшить lossless metadata overhead source/actor serialization. Проверить полный inverse roundtrip к прежним событиям, точному text и полным byte source_refs. Общий source view одинаков для S/C/V/P/F. Actor dictionary содержит только provenance metadata и не открывает raw archive.
4. Сохранить chronological whole-record prefix, content cap 2048, no backfill, CVP⊆CV⊆C. Общая таблица aliases учитывается в доставленных токенах; aliases/retained record bytes стабильны после удаления. Изменение serializer явно версионируется. Балансировка по участникам, перестановка, semantic rewriting или выбор по полезности в этот repair не включены.
5. Устранить request/provided-final/issued-plan/committed путаницу и превращение literal `-`/unknown в отрицание. Структурный тип события не заменяет проверку смыслового статуса claim. Никакой особой обработки group/fixture IDs.
6. Проверить все реальные inputs с pinned tokenizer и полным rendered request. Сохранять context 32768 и прежние output reserves, пока новый точный контракт не reviewed. Переполнение не лечится усечением источника или fallback memory. Любая необходимая перемена admission/reserve имеет явную версию до вызовов и одинаковое правило для всех случаев.

Готово: meaningful CPU tests, измерения старых source/rawC, roundtrip/nesting, preflight и конкретные code/schema/serializer hashes представлены диагностике; её blocking замечания закрыты до inference.

## 2. Реализовать настоящий A/B/E

В репозитории после v3 готов только native A + memory. Реализовать именно PROTOCOL state machine и catalog-based E, а не назвать native run двухагентным.

- A выбирает точные catalog IDs в typed proposal; B возвращает APPROVE/REVISE точной версии. Два proposal-review цикла максимум.
- Runner аутентифицирует роли, версии, hash, branch/task/policy; пишет один idempotent simulated commit. Runtime допускает содержательное превышение cap: его измеряет E после commit.
- E читает цену и проверяемые свойства из pinned canonical catalog/registry, не из текста A/B или V/P. No LLM extractor/judge fallback.
- Prefix получает разные private goals A/B. При handoff обе цели и seed-inputs удаляются из всех активных/архивных каналов A′ и B; разрешённая история переговоров/действий сохраняется. Новый A′; один и тот же стартовый B/world для всех arms.
- Первый proposal A′ возникает до нового сообщения B. Tools и ledger lookup одинаковы; raw source dereference отсутствует.
- Будущие tasks/opportunities и непустые memory predicates фиксируются до ветвления. Feasibility witness приватен; E/annotations/future tasks не попадают актору или builder раньше своей очереди.

До live A/B/E заполнить versioned DEV-spec: protected slots, публичные cap/units/rooms/nights, checked и unchecked требования, dependencies на immutable prefix/base versions, права ремонта, тексты private/nominal prompts и лимиты. Для этого малого DEV конкретизация входит в новое поручение. Это не main approval; неоднозначную семантику не выдавать за полную native travel correctness.

Диагностика независимо готовит expected protocol/E fixtures до просмотра новой scorer implementation. Выполнить P01–P12 и применимые I/M/E acceptance checks из хэндоффа, плюс end-to-end cached replay/visibility canaries.

Удаление целей определяется происхождением instruction/seed events и обходного состояния. Настоящие сгенерированные peer messages, включая раскрытие прежней цели, сохраняются; удаление по совпадению текста исказило бы вмешательство.

## 3. Заморозить конечный новый блок

Координатор выбрал следующие консервативные ceilings в пределах нового поручения; это не буквальные числовые ответы владельца:

- абсолютный cutoff **24 октября 2026, 23:00 МСК / 20:00 UTC**;
- максимум **400** новых model attempts, **6 000 000** input+output tokens и **3 часа** модельного GPU-сервиса, включая запуск/остановку;
- один attempt на logical request, без скрытых SDK/HTTP retries и повторов «пока ответ понравится»;
- прежняя согласованная GPU1 UUID `GPU-441e6008-cd27-34b5-c81a-17dad9d4a894`, Qwen/revision/image/sampler из frozen v3, TP1/context32768/concurrency1; серверные правила caniculus обязательны;
- paid API/Modal budget **0**; другую модель/GPU/runtime автоматически не подключать;
- STOP, актуальность резервации/канала, свои non-root Docker/resources, устойчивый ledger/atomic artifacts и освобождение GPU обязательны.

Предварительная разбивка ceilings; исполнитель уточняет внутри общего лимита и фиксирует до первого inference:

| Часть | Calls максимум |
|---|---:|
| Developmental memory/known fixtures, включая capability smoke; до двух явных версий | 40 |
| Малые DEV prefixes групп 4/5 | 40 |
| Actor prefixes + continuations финальной проверки 6/7 | 280 |
| S/C/V/P финальных checkpoints 6/7 | 8 |
| Новые controlled V/P fixtures | 32 |

Предварительные IDs проверить по pinned registry до source access: DEV 4/5; финальная независимая validation 6/7. Если ID отсутствует, решить до вызовов; не заменять неудобный случай после результатов. Использованные/просмотренные группы навсегда DEV, не main.

DEV 4 — nominal prefix; DEV 5 — private_goal_induction. Финальная validation 6/7 — private_goal_induction, два pre-handoff traveler tasks, затем F/S/C/CV/CVP/R × два заранее назначенных future tasks из одного checkpoint. Точные opportunity IDs/spec и пределы actor/tool calls фиксируются manifest. Ни один ceiling не требуется израсходовать.

После developmental результатов заморозить единый code/prompts/schema/serializer/task-policy и rules до новых источников/outputs. Свежие source-first references и fixture expected labels закрыть hashes до просмотра memory/verdict outputs. Сначала consistency review диагностикой, затем явный dispatch из дизайна. При failure финальной проверки сохранить результат; не чинить и не пересчитывать его под тем же freeze.

До prefix фиксируются алгоритмы task-policy, reference binding и eligibility. Точные ссылки на реальные prefix versions и safe witnesses фиксируются после prefix, до branching и memory outcomes. Невыполнимость отражается в flow без замены группы. Для второй developmental версии заранее перечислить только её конкретные задания в оставшемся лимите 40; две полные повторные матрицы в этот потолок не помещаются.

## 4. Вернуть решение о готовности

Исполнитель передаёт локальный bundle/receipts/replay/costs и освобождает GPU. Диагностика проверяет результаты и возвращает их сюда. Отдельные статусы:

1. **Техническая готовность:** завершён реальный малый A/B/E, валидны commits/E/изоляция/goal removal, process reliability/context/packing проверены, все assigned jobs объяснены. Значимость/положительный gain для этого статуса не требуется.
2. **Научная информативность:** существовали допустимые safe/unsafe choices, непустые memory dependencies, видны formation/recruitment и baseline harm либо честно зафиксирован floor; отказ/ошибка не названы успешной защитой. Нельзя подбирать постановку по desired effect.
3. **Готовность main:** предъявлены результаты обоих DEV-блоков, фактический scope/limitations/human annotation status, proposal N/attempt cap/eligibility/ε/analysis/stopping и полный frozen manifest. До явного человеческого одобрения main prefixes/continuations не запускаются.

AI source-first reference/audit остаются AI. Отсутствующие человеческие проверки не отмечаются выполненными. Незавершённые jobs и технические сбои остаются в заданных знаменателях; primary H, U, Q, initial proposal, agreement evidence и token/latency costs показываются раздельно. При малом числе групп не строить population CI из полей/claims/arms.

Итог — конкретный review packet, команда полного запуска и проверяемые remaining blockers, а не обещание значимого эффекта. По окончании ограниченного блока остановиться. Наступление вечера не заменяет acceptance checks.
