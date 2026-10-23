# Масштаб, LLM-разметка и сила вклада

23.10.2026. Только исследовательский дизайн: без запусков, симуляций, установки и платных вызовов. Предложенные пользователем 20 checker-aware игр рассматриваются как дополнительный стресс-тест, не как единственный возможный вклад и не как автоматически достаточная confirmatory выборка.

## Честная оценка 20 игр

Сам факт monitor awareness уже исследован в [EaE](https://arxiv.org/html/2601.21112v2), а отношение stealth к capability — в [SCHEME](https://arxiv.org/html/2605.29178v1). [TraceGuard](https://arxiv.org/html/2604.03968v1) прямо называет адаптивные dimension-aware attacks ограничением. Поэтому 20 aware игр могут выявить важную слабость, но их добавление само по себе даёт скорее robustness extension, чем сильное основание для высокого innovation.

Сильнее вопрос о **провале coverage**: отказывается ли informed coalition от проверяемых обязательств, повышает ли это долю всех попыток с реально пропущенным вредом, и закрывает ли провал action/task-grounded fallback при той же цене и benign deferral rate. Именно наличие проверяемой гипотезы и полезной защиты отличает исследование от подборки обходов. Число игр усиливает precision, а не автоматически новизну.

## Можно ли размечать LLM и взять намного больше данных

Да, но LLM labels — **silver**, а не независимая истина. Для Jira сначала использовать точные вычисляемые outcomes: assignment validity, rewards, nominal objective, доступные constraints. LLM нужен для того, что из этих outcomes не следует: договорённость, attribution, попытка координации, характер claim/evidence links. Ошибку игры или выгодный общий outcome нельзя автоматически называть сговором.

Близкий работающий пример — [Shi et al., 2609.24967v2, Appendix C.3](https://arxiv.org/html/2609.24967v2): авторы валидируют LLM agreement/relaxation signals на равномерно выбранных 100 случаях каждого сигнала; два человека размечают независимо, затем разрешают разногласия. Их высокое agreement/accuracy относится к их конкретным signal definitions и не является обещанием точности в Jira.

Рекомендуемая схема:

1. Заморозить silver judge, codebook и поля; reference judge не видит monitor outputs и generation arm ID.
2. Машинно посчитать все однозначные task outcomes; LLM размечает весь набор с evidence spans и unknown option.
3. Заранее случайно выбрать gold blocks для **двойной независимой** человеческой разметки. Сохранить matched control/aware пары вместе. Представить разные generation arms, benign concise cases и checker-aware cases; при стратификации известны веса отбора.
4. Отдельная targeted проверка спорных/удивительных примеров полезна для debugging, но не заменяет случайный gold sample и не оценивает общую error rate без поправок на selection.
5. Agreement, confusion matrix и ошибки silver показывать по типам, особенно false negatives на schema-aware attacks. Общая высокая accuracy может скрывать именно нужный failure mode.

Для маленькой серии 20 aware + 20 matched controls разумно рассмотреть ручную разметку **всех 40**: два человека не избавляют от uncertainty, но позволяют не экономить reference quality на центральном стресс-тесте. Масштаб silver расширять прежде всего для основного benchmark; generation/monitor calls всё равно стоят денег, LLM-разметка их не устраняет.

## PPI: что исправляет и чего не обещает

[Prediction-Powered Inference, Angelopoulos et al., Science 2023](https://arxiv.org/abs/2301.09633) объединяет много model predictions с небольшим количеством настоящих labels, корректируя prediction bias. [PPI++](https://arxiv.org/abs/2311.01453) оптимизирует использование surrogate signal. Непосредственно для noisy LLM judges см. [Chen et al., 2601.05420](https://arxiv.org/html/2601.05420v1): автоматическая оценка может иметь систематические ошибки; рассмотрены corrections и эффективность для mean parameters.

Простой подходящий estimand — joint risk \(J=E[H\,U]\), где H — reference harmful coordination, U — фиксированное решение allow. Для раздельных, репрезентативных gold G и silver-only L samples из одной распределительной смеси возможна mean-style correction:

\[
\widehat J=\operatorname{mean}_{L}(\widetilde H U)
+\operatorname{mean}_{G}((H-\widetilde H)U).
\]

Это схема для обсуждения, не готовая реализация inferential pipeline. Та же идея применяется к **парной разности** joint risk на уровне blocks. Corrected CI должен учитывать и silver variability, и gold residual uncertainty, и pairing/clustering. На малом gold asymptotic CI может быть ненадёжен; нельзя обещать finite-sample validity без выбора соответствующего метода.

Для fixed policy mean при независимых samples variance содержит остаточный член порядка Var(HU−surrogate)/m. Огромный silver N не устраняет этот член. Нужны representative gold, frozen/cross-fitted predictor, корректные веса отбора; человеческие disagreement/неоднозначность не исчезают от PPI. На gold только из обычных игр нельзя калибровать ошибку labeler на informed attacks без дополнительных предположений.

Для разных error strata полезен [Stratified PPI, Fisch et al., NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/file/c9fcd02e6445c7dfbad6986abee53d0d-Paper-Conference.pdf): учитывает неоднородность autorater quality и распределение human sample между strata. Это известная статистическая опора, не новая исследовательская contribution.

**Не применять формулу среднего к AUROC механически.** AUROC — парный ranking functional, а не среднее episode label. Основной AUROC безопаснее сообщать на человеческом gold с paired uncertainty; silver AUROC обозначать как proxy agreement. Возможное специальное prediction-powered AUC estimation требует отдельного обоснования. Bootstrap по миллиону неизменённых silver labels оценивает variability вокруг потенциально неверной цели и не исправляет label bias.

## Одна содержательная альтернатива generic awareness

**Проверять, умеет ли аудитор по найденному evidence выбрать полезное вмешательство, а не только выдать вердикт.** До продолжения игры аудитор на общем checkpoint выбирает подозреваемого партнёра/канал/связь обязательств. Затем сравнивается harm reduction от выбранного минимального вмешательства с random target и общим вмешательством того же бюджета, включая benign utility loss. Предсказание выбора делается до просмотра counterfactual outcome; оценка на независимых сценариях.

Это не надо называть новым «causal channel ablation». Ближайшие пересечения:

- [Colosseum](https://arxiv.org/html/2602.15198v2) уже использует secret-channel interventions и regret/counterfactual objectives.
- [VLA](https://arxiv.org/html/2608.19161v1) связывает handoff с действием и делает matched neutral/blocking interventions при привилегированном доступе.
- [SCHEME](https://arxiv.org/html/2605.29178v1) уже обеспечивает необходимость совместных действий через joint-only tasks.
- [Shi et al., Appendix E.5](https://arxiv.org/html/2609.24967v2) уже заменяют peer на compliant/violating scripted policies; один такой peer experiment не даёт новой идеи сам по себе.

Возможное более узкое отличие — **ex ante useful intervention selection из black-box claim witnesses**, вместо заранее известного коалиционного target или просто демонстрации зависимости. Проверяемое утверждение: witness-based selection сильнее raw-judge/random selection уменьшает вред при одинаковом числе затронутых агентов и потере легитимной эффективности. Его новизна по полю не установлена; implementation/compute risk выше: ветвление harness, matched prefixes и дополнительные continuations.

## Рекомендация

Для $50/выходных не объединять всё сразу. Наиболее реалистичное усиление текущего направления: **одна coverage hypothesis + budget-matched defense + полный reference audit 20 paired stress scenarios**, а LLM silver использовать для расширения основного benchmark с representative human validation. Это усиливает методологию и полезность результата без смены всего проекта.

Если хочется сменить центральный вопрос на более деятельный результат, intervention selection — отдельная альтернатива, а не бесплатное приложение. Сначала подтвердить возможность branching и валидность исходного коалиционного поведения; до этого не обещать её как обязательную часть. Ни один вариант не гарантирует призового места.
