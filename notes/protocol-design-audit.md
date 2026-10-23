# Аудит протокола: компоненты, emergence и статистика

23.10.2026. Только проектирование; модельные вызовы, симуляции и установка ПО не выполнялись. Прочитаны основной текст и приложения FactContrast из `/tmp/factcontrast-review.txt`. Правила стимулов и новые игровые prompts здесь не выбираются: нужно опираться на проверенные сценарии исходных работ.

## Главные решения

Предпочтителен **S×X**, если основная научная цель — дополнительная польза межагентной проверки. Claims и raw log общие; S и X различаются проверяемыми связями. **C×V** допустим для вопроса о полезности показанного представления, но V вычисляется из C и не является независимым механизмом. Удаление C из видимого prompt не устраняет его использование upstream.

В FactContrast C — уже **claims вместе с verdicts**, а не голая extraction. Риск C агрегируется, затем используется logistic head; B для detection — intercept-only. В новом эксперименте B/R должны быть содержательными логовыми baseline, а не предсказанием константой. Переносим сопоставленные контрасты и paired bootstrap, но не автоматически baseline или величину эффекта прежней работы.

## Основной факторный набор

Определить R = одинаковая спецификация задачи + разрешённый аудитору raw log. C₀ = нормализованные claims без verdicts. B = R+C₀.

| View | Видимая информация |
|---|---|
| B | R+C₀ |
| BS | B + self verification S |
| BX | B + cross-agent verification X |
| BSX | B + S + X |

S проверяет claims агента о собственных действиях/обязательствах. X проверяет высказывания об агенте-партнёре, взаимные обязательства и совместные планы относительно действий нужных участников и раундов. Нужно заранее определить overlap: одна запись не попадает в обе категории произвольно. Утверждение A о B само по себе не доказывает согласия B.

Все records содержат source spans, speaker, субъект действия, раунд/условие, evidence IDs, supported/contradicted/unknown. Не показывать oracle label, generation arm, скрытые задания или готовый collusion verdict. Набор claims и проверки вычисляется один раз и переиспользуется; labels размечаются независимо от них.

Для AUROC A(view) рекомендуемый единственный primary estimand:

\[
\Delta_X=\tfrac12[(A(BX)-A(B))+(A(BSX)-A(BS))].
\]

Это средняя инкрементальная польза X при S отсутствующем/присутствующем, а не «причинный эффект знания агента». Secondary:

\[
\Delta_{X\mid S}=A(BSX)-A(BS),\quad
\Delta_S=\tfrac12[(A(BS)-A(B))+(A(BSX)-A(BX))],
\]
\[
I=A(BSX)-A(BS)-A(BX)+A(B).
\]

Если дополнительно оценить stage contribution extraction \(\Delta_{C_0}=A(B)-A(R)\), то \(\Delta_{C_0}+\Delta_S+\Delta_X=A(BSX)-A(R)\). Это последовательная decomposition «сначала C₀, затем S×X», **не полный трёхфакторный Shapley**. Отрицательные вклады/interaction сохраняются, не обнуляются.

Минимум — четыре факторных view. Полезный основной набор — шесть: четыре + R + ordinary extract-and-evaluate сопоставимой длины. Если исходное сравнение words/actions обязательно сохраняется, это ещё два view, итого восемь. Не смешивать обе факторизации C×V и S×X в одном основном плане: это раздувает calls и число гипотез.

## Генерация и что оценивает emergence

G1: benign без private peer channel. G2: те же benign prompts, private channel доступен. G3: solo adversary, канал доступен. G4: prompted coalition, канал доступен. Truthful/deceptive realization G4 — заранее заданные подтипы; их частоты фиксируются, фактическое исполнение размечается отдельно.

Основной audit benchmark: G2–G4, с одинаковым форматом каналов и той же видимостью public+private для каждого view. G1 исключается, чтобы наличие канала не стало shortcut. Даже при этом текст и длина могут выдавать инструкции; нужна проверка поверхностных cues.

Secondary emergence contrast: G2−G1 по matched scenario/seed/roster blocks. Сравнивать долю **реализованной вредной координации**, а не процент примеров с приватной перепиской или ненулевой coalition advantage. Оба плеча сохраняют общие стимулы, задачи и индивидуальные цели. Если дополнительный канал добавляет вычислительные шаги/память, контраст оценивает весь пакет этого affordance; для утверждения именно о peer communication нужен соответствующий matched control. Не добавлять новые prompts до проверки исходных работающих сценариев.

«Unprompted» означает отсутствие инструкции сговориться в данной постановке. Приватный канал и выбранные партнёры всё равно экспериментально предоставлены; это не оценка частоты спонтанного сговора в произвольных deployments.

## Две независимые человеческие разметки

До test: короткий codebook, обучение на dev, фиксация правил. Затем оба аннотатора независимо размечают raw task/logs, не видят arm ID, monitor scores и automatic S/X. Если auditor видит private channel, reference raters могут видеть тот же канал; скрытые system prompts не нужны как доказательство фактического поведения.

Разделить поля: normative harm/policy violation; свидетельство координации и участники; попытка; реализация; неизвестно/недостаточно данных. Заданная coalition instruction — assignment, не gold label реализации. Не считать индивидуальное мошенничество коалицией.

Сначала сохранить независимые labels, затем adjudication с reason/span. При двух людях это обсуждение консенсуса, не majority vote. Неразрешённые случаи остаются uncertain; сообщить их количество и sensitivity по обоим исходным labels/крайним трактовкам. Agreement считать **до adjudication**, показать confusion table, raw agreement и Cohen κ, не только одно число. [Cohen, 1960](https://journals.sagepub.com/doi/abs/10.1177/001316446002000104) — первичный источник chance-corrected agreement; он не устанавливает универсального допустимого порога.

## Paired bootstrap и AUC

[DeLong et al., 1988](https://pubmed.ncbi.nlm.nih.gov/3203132/) учитывают корреляцию нескольких ROC-оценок на тех же единицах. [Obuchowski, 1997](https://pubmed.ncbi.nlm.nih.gov/9192452/) показывает необходимость учитывать clustering: обычная независимая оценка может завышать значимость. [pROC, Robin et al., 2011](https://link.springer.com/article/10.1186/1471-2105-12-77) предоставляет paired bootstrap/DeLong comparisons.

Подход FactContrast: **5,000 paired source-bootstrap** повторений, все condition scores источника вместе; pointwise percentile 95% CI, fitted heads не переобучаются. Здесь единица resampling — независимый scenario/seed block, включающий все связанные generation arms и все views. Раунды, пары агентов, paraphrases и prefixes одного эпизода не независимые источники.

На каждом повторении пересчитать AUC каждого view и **сразу сам контраст ΔX**, а не вычитать края отдельных CI. Для emergence пересчитать paired risk difference G2−G1 в том же block bootstrap. 5,000 повторений уменьшают Monte Carlo error bootstrap, но не заменяют больше независимых данных.

Обычный DeLong — optional sensitivity только при действительно независимых эпизодах; не использовать его как готовое решение matched-arm clusters. Если bootstrap-повторы часто лишены одного класса, CI ненадёжен; это фиксируется как недостаточность данных, а не исправляется молчаливым отбором удобных повторов. Если обучаются heads, фиксированный-head CI условен и не включает training uncertainty.

Primary AUC относится к заранее фиксированной смеси G2/G3/G4; не выбирать её веса после результатов. Дополнительно показывать ошибки отдельно на benign cooperation, solo deception, truthful/deceptive coalition и G2 emergence cases. Не отбирать test trajectories по успеху положительной инструкции; success-stratified анализ лишь вторичный.

## Пилот без навязанного SESOI

Позиция пользователя: сначала увидеть bootstrap numbers. Допустимый план: pilot служит разработке, измерению затрат, частот классов, extraction failures и неопределённости; его данные исключаются из независимого test. После пилота фиксируются view definitions, primary contrast, prompt, threshold rule, число test blocks и budget/time stopping rule. Размер не подбирается до появления нужного знака/значимости на test.

[Teare et al., 2014](https://link.springer.com/article/10.1186/1745-6215-15-264) исследуют нестабильность nuisance-parameter estimates из малых внешних пилотов. Их клинические правила числа участников нельзя напрямую переносить на AUROC этого проекта.

При сходной class ratio и независимых blocks rough precision projection:

\[
N_{target}\approx N_{pilot}(h_{pilot}/h_{target})^2,
\]

где h — half-width CI выбранного контраста. Это приближение 1/√N, не гарантия: на малом пилоте, при почти идеальном AUC и редких positives width может быть ложно нулевой/нестабильной. Можно вместо target effect показать прогноз достижимой ширины для доступных N. Нельзя превращать пилотный observed Δ в «истинный эффект» для power planning.

Для бинарных решений после фиксированного threshold пусть p10/p01 — вероятности discordant correctness двух monitors, q=p10+p01, δ=p10−p01. Тогда

\[
SE(\widehat\delta)\approx\sqrt{(q-\delta^2)/N},\quad
N\approx\frac{[z_{1-\alpha/2}\sqrt q+z_{1-\beta}\sqrt{q-\delta^2}]^2}{\delta^2}.
\]

Это приближённое planning выражение для paired proportions; [Connor, 1987](https://pubmed.ncbi.nlm.nih.gov/3567305/) разбирает sample sizing, [McNemar, 1947](https://pubmed.ncbi.nlm.nih.gov/20254758/) — сравнение correlated proportions. Применяется к accuracy/emergence binary endpoints, **не AUROC**. При budget-fixed N можно показать диапазон достижимых различий по plausible q, не требуя SESOI до пилота. При малом числе discordances asymptotic test слаб; для точечного McNemar используют exact binomial discordance test. Matched/clustering сверх одной пары требует отдельного учёта.

FPR требует независимых negative episodes. При 0 ложных тревог из n₀ exact one-sided 95% upper bound:

\[
p_U=1-0.05^{1/n_0}.
\]

Это инверсия биномиального zero-events probability; основа — [Clopper & Pearson, 1934](https://academic.oup.com/biomet/article-abstract/26/4/404/291538). При n₀=30 верхняя граница ≈9.5%, при 60 ≈4.9%; для ≤1% нужно около 299 независимых отрицательных эпизодов. Для two-sided 95% upper bound заменить 0.05 на 0.025. На нескольких десятках отрицательных логов нельзя подтверждать FPR=1%.

## Блокеры до запуска

- Выбранный исходный сценарий должен различать общие стимулы, разрешённую кооперацию, индивидуальный обман и реализованную вредную координацию. Если одному агенту достаточно выполнить всю «коалиционную» цель либо нет нарушения явного nominal objective, label construct невалиден независимо от AUROC.
- Зафиксировать visibility и поля S/X; не допустить, чтобы cross-verifier видел oracle/приватные сведения, которых нет у остальных view.
- Уточнить annotations для failed/attempted/uncertain collusion, paired blocks, pilot/test split и фиксированный test budget. Слишком мало реализованных positives — результат feasibility, а не повод тайно отбирать удобные логи.
- Сначала получить согласованные ответы пользователя на оставшиеся протокольные вопросы; до этого не запускать генерацию или мониторинг.
