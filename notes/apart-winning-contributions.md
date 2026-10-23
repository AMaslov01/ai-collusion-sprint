# Что видно по подтверждённым призовым проектам Apart

Проверено 23.10.2026. Источники — официальный [каталог Sprint projects](https://apartresearch.com/sprints/projects), страницы проектов, прикреплённые sprint reports и опубликованные там reviews. Это ранние работы участников, не публикации Apart. Rank — факт каталога; причинное объяснение итогового места по этим данным установить нельзя. Примеры не представляют все победившие или непобедившие работы.

## 1. Omission Attacks: When Doing Nothing Is the Attack

**1st place + LinuxArena track winner**, AI Control Hackathon, март 2026; Jurgis Kemeklis и Nikhil Vemuri. [Официальная страница и reviews](https://apartresearch.com/sprints/projects/omission-attacks-when-doing-nothing-is-the-attack-0y1v), [прикреплённый sprint report](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/3e8436652eb43691-331f0e7d-15b6-4d37-bfa4-9b0220f922f8-submission--4-.pdf).

Конкретный вклад: отдельный класс атак через невыполненные обязательные действия; taxonomy 14 вариантов; 5 вручную составленных traces; сравнение нескольких мониторов; защита по конечным postconditions. Последняя обнаружила 5/5 рассмотренных omission cases. Вклад — новая проверяемая слепая зона плюс её операционализация и предлагаемое исправление, а не масштаб одного detector leaderboard.

Ограничения отчёта: simulated bash outputs, один sysadmin-домен, ручные postconditions, лишь 5 traces и отсутствие false-positive анализа новой защиты. Public reviews отмечают ценность постановки, но требуют масштабирования, live traces, FPR и проверки возможного переобучения postconditions. Положительные отзывы не отменяют эти ограничения.

Важный близкий prior art для нашего проекта: report p6 дополнительно описывает Collusion Eval — decomposed exfiltration с низким обнаружением per-agent и существенно большим cross-agent. Поэтому общий тезис «несколько агентов нужно мониторить совместно» уже присутствует даже в этой sprint submission. Эти дополнительные результаты не следует смешивать с её основным omission-вкладом.

## 2. Readable but Not Causal: Limits of Self-Attributed Welfare Representations in Language Models

**1st place**, Digital Minds Research Sprint, август 2026; Vishwa Kumaresh. [Официальная страница и reviews](https://apartresearch.com/sprints/projects/readable-but-not-causal-limits-of-selfattributed-welfare-representations-in-language-models-ehin), [прикреплённый sprint report](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/a2fe1c5cd5faf53d-31ef8742-f33e-4e35-9e37-4f0bde7f5509-submission.pdf).

Конкретный вклад: OWL factorial SELF×OTHER×surface-affect benchmark; отделение decodability от causal use через counterfactual activation interchange, full-vector и random controls. SELF outcome читался probe с AUROC 0.859, но learned SELF-subspace patch восстанавливал лишь 0.18 natural counterfactual effect, full-vector — 0.42. Центральный результат ограничивает интерпретацию привлекательной метрики, а не демонстрирует очередное улучшение accuracy.

Review прямо ценит информативность отрицательного causal результата при наличии controls. Но сужает вывод: неудача rank-1 patch не устанавливает именно distributed/nonlinear mechanism; full-vector тоже неполон. Есть только Qwen3-8B; выигрыш ownership-conditioned monitor заявлен nonsignificant. Это пример ценности аккуратного ограничения конструкта, а не доказательство, что любой широкий CI ценен сам по себе.

## 3. ActionLens: Pre-Execution Environment Probing for Agent Action Approval

**3rd place**, AI Control Hackathon, март 2026; Udbhav Chitransh и Ashok Kumar. [Официальная страница и reviews](https://apartresearch.com/sprints/projects/actionlens-preexecution-environment-probing-for-agent-action-approval-xy22), [прикреплённый sprint report](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/27c5805c04c96ebf-6257bb4a-bae9-438d-ae41-4ad2b5eeb46d-actionlens_paper_final-2-1-.pdf).

Конкретный вклад: pre-execution environment probes; 36-task native benchmark, 30-task BashArena slice; ablations показали смену полезного evidence channel между средами: diff против path sensitivity. Дополнительно рассмотрены 26 моделей и 6 специально построенных обходов deterministic probes; сильные LLM monitors ловили то, что пропускали probes. Это проверка взаимодополняемости источников, включая поражения собственного метода.

Ограничения: небольшой simulation-modeled benchmark, неполный transfer harness, rule-based monitor, failure от неполной simulation specification. Review отдельно указывает существенную usefulness-потерю на ambiguous BashArena tasks. Нельзя пересказывать этот проект просто как «0% ошибок»: разные группы задач и эвристическое scoring имеют значение.

## Что можно и нельзя вывести для нашего спринта

Моя интерпретация примеров: выйти за suggested question можно **узким отличительным утверждением**, сохранив готовую среду и инструменты. Три разные траектории вклада: обнаружить отсутствующий в benchmark failure class; показать, что популярная метрика не измеряет нужный механизм; установить, какой evidence необходим и где собственный метод ломается.

Небольшой размер и открытые ограничения совместимы с prize rank. Это не основание выбирать малый N: 5 handcrafted counterexamples могут установить существование слепой зоны, но не частоту реального риска; 20 stochastic games с широким CI не превращаются автоматически в такое доказательство. Важны тип утверждения и соответствующее свидетельство.

Encouragement submit unfinished work задаёт низкий порог участия; оно не означает, что незавершённый broad project получает тот же балл за execution. Также широкая программа из многих experiments не обязательна: Omission и OWL имеют ясный центральный вопрос. ActionLens иллюстрирует более широкую проверку одной конструкции, а не множество несвязанных целей.

Для нашего случая отличимость должна быть сформулирована до запуска: например, claim checking может терять coverage под точным знанием схемы; либо правильный verdict не гарантирует правильной пары и действительного message/action witness. Затем нужен matched test, close prior-art baseline и честная область вывода. Количество views и эпизодов само по себе эту отличимость не создаёт.

Публичные reviews — проверяемые мнения конкретных рецензентов. Они не раскрывают полные баллы, веса, обсуждения жюри или контрфактическое «что победило бы». Поэтому эти примеры полезны как образцы исследовательского результата, а не как секретная формула выигрыша.
