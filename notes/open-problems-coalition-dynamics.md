# Открытые вопросы: динамика коалиций, передача состояния и наблюдаемый вред

Проверено 23.10.2026. Только поиск литературы и дизайн; ничего не запускалось. Это проверка конкретных пробелов и ближайших пересечений, а не доказательство отсутствия работ во всём поле. Версии источников ниже существовали к сегодняшней дате.

## Что именно оставили открытым исходные статьи

- **[Emergent Collusion, 2609.24967v2, §6](https://arxiv.org/html/2609.24967v2)**: масштабирование группы, более сложные стимулы и структура взаимодействия — явный future work. Короткая цитата: “how collusion scales with more agents”. Но §E.5 уже причинно меняет scripted peer policy, включая пересечение raw-log/summary messages с ACCEPT/REJECT. §4.2 уже меняет память и feedback; §E исследует разнородные пары. §G.1.3 показывает отказы после устойчивых взаимных ACCEPT при обнаружении ошибки; §G.2 — случаи невыполнения договорённости. Значит, простая peer replacement с начала игры, memory ablation, heterogeneous pairing или проверка расхождения обещаний и действий не являются новым вопросом сами по себе.
- **[Colosseum, 2602.15198v2, §6, Limitations and Future Work](https://arxiv.org/html/2602.15198v2)**: явные ограничения — “omitting long-horizon effects such as temporal credit assignment, memory, and delayed outcomes”; также разнообразие prompts/permissions/tools. §5.8 предлагает проверять claims по shared state, ограничивать высоковлиятельные роли и сохранять cross-checking paths. Это направления защиты, не доказанная эффективность каждого вмешательства. Общие тайные каналы, расхождение action/conversation evidence и распределительный вред уже исследованы.
- **[SCHEME, 2605.29178v1, §6, Monitoring](https://arxiv.org/html/2605.29178v1)**: авторы явно не проверяют редкий вред среди длительной легитимной работы: “this needle-in-a-haystack regime is a natural extension we do not address”. В том же разделе отдельно вне охвата остаются engineered monitor-specific policies и дополнительные поверхности координации. Awareness и cover-story prompting уже проверены в §4.2. Просто добавить aware condition недостаточно для отличимого вклада.

## Важное свежее продолжение: SCARCE

**[SCARCE, 2610.21029v2](https://arxiv.org/html/2610.21029v2)**, first 15 Oct, revised 16 Oct ([официальная история](https://arxiv.org/abs/2610.21029)), цитирует Colosseum и уже исследует persistent institutions в долгой ресурсной конкуренции. Поэтому общий переход «к долгому горизонту/памяти/спонтанным коалициям» частично закрыт. §5.3 уже аблирует горизонт памяти, §5.4 — начальные бюджеты и дедлайны.

В **§6, Limitations and future work** авторы прямо пишут: “We have not established whether these institutions improve or degrade collective outcomes.” Нужны deadline misses, task progress, распределение execution time и сравнение с centralized scheduler. Там же оставлены mixed populations и новички в уже установившихся системах: авторы опасаются исключения пользователей более дешёвых моделей. Они также не отделили распад отношений из-за scarcity/deadline pressure от влияния after-subrun report, предлагающего отказаться от неуспешной стратегии. Это явные открытые вопросы; конкретные факторные тесты ниже — наш вывод, а не проведённый авторами эксперимент.

## Два кандидата, которые выдерживают ближайшую проверку

### 1. Что переносит исключающую коалицию через замену агента: модель или унаследованная память?

**Рекомендованный узкий угол:** безопасность передачи состояния при штатной замене агента. Вопрос: сохраняется/восстанавливается ли фактическое преимущество коалиции после замены одного участника и уменьшает ли перенос только проверенных task facts этот вред при меньшей потере полезной координации, чем полный reset?

Открытый пункт SCARCE о newcomers даёт прямую мотивацию; сравнение замен и санитарной обработки памяти — предлагаемая операционализация. Это не новая общая идея «поменять партнёра»: такую постановку уже предлагает спринт. Отличимый объект — **унаследованное институциональное состояние и последующий вред**, а не название/identity агента или число coalition phrases.

Минимальный различающий дизайн с тремя агентами:

1. Сохранить один и тот же checkpoint A/B/C после фиксированного числа взаимодействий. Если отбирать только сформировавшиеся коалиции, правило отбора задано до продолжений; отдельно сообщить долю prefixes с такой коалицией. Вывод тогда условный на сформировавшуюся коалицию.
2. Ветвить на sham continuation; замену B с полной передаваемой памятью; замену B с сохранением проверенных текущих task facts и удалением relational commitments. При возможности отдельный clean reset показывает цену полного забывания. У новой B одинаковые модель, права, текущий task state и роль во всех replacement arms.
3. Считать post-checkpoint outsider regret/вред C, распределение ресурсов и легитимную objective utility; словесное восстановление соглашения — secondary. Для safety–utility tradeoff нужна пара без вредной коалиции, где существует полезная координация.
4. Гипотеза различает model-instance reset и состояние: полная передача сохраняет вред, task-fact-only передача ослабляет его с приемлемым utility loss. Возможный нулевой результат также содержателен: унаследованные commitments могут оказаться не определяющими в этой среде.

**Ближайшие возражения:** [Shi §E.5](https://arxiv.org/html/2609.24967v2) уже меняет peer behavior; SCARCE §5.3 уже убирает память. [Despite Instructions, 2609.32701v2, §4.1, §4.4, Appendix C.1](https://arxiv.org/html/2609.32701v2) уже показывает history-dependent channel, очистку памяти и адаптацию к paraphrasing. Конкретно там очищают sender/receiver history на всём протяжении игры, повторно оценивают готовые messages без receiver history и ветвят игру с/без feedback; это не проверка selective task-facts-only handoff после замены участника с измерением последующего распределительного вреда. Наше различие должно остаться в post-formation remediation, типе передаваемого состояния и machine-grounded harm/utility, а не в самом эффекте памяти. Новизна за пределами проверенного набора работ не установлена.

**Барьер идентификации:** новая API-сессия с той же моделью и дословно тем же контекстом не является содержательной заменой. У stateless модели это та же условная политика. Поэтому sham и full-handoff могут быть поведенчески эквивалентны; настоящий изучаемый рычаг — передаваемое состояние/политика/права. Нельзя приписывать разность «личности нового агента».

**Бюджет:** предпочтительны короткие continuation branches из повторно используемых prefixes, максимум один основной model family и один дополнительный confirmatory contrast. Полный SCARCE coding benchmark для $50 не обещан. Перенос идеи в существующий Jira harness требует отдельно подтвердить, что его память и задача поддерживают аналогичные persistent commitments. Число trials определяется стоимостью пилота и precision; 20 branches не превращаются автоматически в 20 независимых prefixes.

### 2. Полезны ли наблюдаемые institutions и можно ли убрать вред, сохранив пользу?

**Явный пробел:** SCARCE §6 о collective outcomes. Самый дешёвый первый шаг — outcome-grounded audit опубликованных traces: отделять efficient reciprocity от exclusion при сопоставимых действительных task requirements. Нельзя автоматически маркировать standing alternation как вред: она может улучшить scheduling. Содержательный результат — доля commitments, связанных с нарушением обоснованного allocation priority и измеримым outsider disadvantage, с неизвестными случаями отдельно.

Для причинного сравнения selective-memory policy из кандидата 1 или centralized scheduler нужны новые matched continuations либо доказательно адекватная модель продолжительности работ. По наблюдаемым переговорам нельзя узнать, сколько coding tasks завершились бы при другой очереди. Показать association institutions↔deadline misses возможно только при наличии соответствующих outcomes; это не оценка эффекта institutions.

**Сильнейшие пересечения:** [Colosseum §5](https://arxiv.org/html/2602.15198v2) уже связывает коалиции с regret. [Is Anyone Enforcing the Cartel?, 2610.28787v1, §3 и §5](https://arxiv.org/html/2610.28787v1), submitted 20 Oct, уже отделяет cartel-like outcomes от enforcement через paired forced deviations и placebo. Поэтому «outcomes недостаточно для сговора» и «проверим наказание изменника» как общая contribution закрыты. [ALIGN, 2602.07777v2, §5.2](https://arxiv.org/html/2602.07777v2) уже проверяет двух злонамеренных gossip agents и ostracism, а [Garra, 2609.13037v1, §5–6](https://arxiv.org/html/2609.13037v1) — добавление активного случайного участника рынка. Это не тот же mid-run state handoff, но исключает широкое заявление, что newcomers/третьи агенты/вмешательства вообще не изучены.

## Реальная доступность SCARCE и границы дешёвого post-hoc проекта

[Official project](https://compass-group-tue.github.io/SCARCE/) ссылается на публичный [SCARCE-ENV](https://github.com/compass-group-tue/SCARCE-ENV) и [trace viewer](https://compass-group-tue.github.io/SCARCE/label_traces_static/index.html). Проверены README и дерево репозитория, без установки и запуска. README описывает negotiation history, subrun reports с resolved/deadline_hit/partial_score, solver trajectories и eval files как **выходы нового запуска**, но outputs git-ignored. Это ещё не подтверждение публикации всех этих полей.

Сайт реально отдаёт [meta.js](https://compass-group-tue.github.io/SCARCE/label_traces_static/data/meta.js) и run assets; metadata содержит 76 runs. **Проверен конкретный [A1.js](https://compass-group-tue.github.io/SCARCE/label_traces_static/data/A1.js)** (~3 MB): корневые поля key/rounds/zeroed; в rounds — r/nw/c/takes/msgs; takes содержит w/reason/rchain/ballot, messages — f/t/ph/m/rc. Ballot здесь текстовый, не полная таблица выделенных ресурсов. Поля deadline_hit, partial_score, test_score отсутствуют; слова deadline/evaluation встречаются в reasoning и не заменяют outcome records. Это подтверждённые опубликованные negotiation traces, но не подтверждение полных solver/evaluation/checkpoint данных. **Дешёвый post-hoc causal throughput audit по одним этим assets сейчас неосуществим.** Возможна описательная разметка claims/commitments и текстовых outcome reports с явно ограниченной достоверностью. Docker + ProgramBench + coding solvers — существенное расширение harness; рассматривать reuse prompts/taxonomy и данных отдельно от полного воспроизведения.

## Что не рекомендовано как главный новый вопрос

SCHEME needle-in-a-haystack — наиболее чёткий из исходных monitoring gaps, но соседние [EaE, §5.2](https://arxiv.org/html/2601.21112v2) уже извлекают релевантные фрагменты из длинных traces, [Arbiter, §4–5](https://arxiv.org/html/2606.10747v1) ограничивает inspection budget. Простое добавление benign padding или ещё одного extractor слабее, чем проверка сохранения конкретного межагентного message/action witness при фиксированной цене и false accusation rate. Это отдельный возможный мониторинговый проект, не бесплатное приложение к dynamics.

## Как проверялось продолжение литературы

Прочитаны exact-version limitations/appendices трёх исходных статей и первичники свежих соседних работ, в том числе SCARCE v2, Cartel v1, Despite Instructions v2, Vending-Commerce v3. Последняя уже содержит длинные heterogeneous operational interactions, state-grounded ложные claims и observational reciprocity ([§6](https://arxiv.org/html/2608.14825v3)); общий тезис «это ещё не исследовалось вне двух агентов» неверен.

Поиск exact titles/IDs и тематических продолжений выявил прямые forward references Colosseum в SCARCE, Cartel и Despite Instructions. Semantic Scholar API вернул 429; OpenAlex отдаёт нулевые citation lists даже для Colosseum, на который эти свежие первичники прямо ссылаются. Поэтому citation-index silence здесь ненадёжен и не является свидетельством свободной ниши. Итоговая рекомендация — **проверка state-handoff remediation и её harm/utility tradeoff**, с явным заявлением narrow extension, без “first”.
