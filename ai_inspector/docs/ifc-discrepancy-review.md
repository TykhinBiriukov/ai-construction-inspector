# Контракт проверки несоответствий IFC и фактической съёмки

**Статус на 31.10.2026:** реализован код двух отдельных A2A-агентов и контракт v1 для проверки предоставленных кандидатов. Bob остаётся существующим оркестратором. Geometry Inspector собирает закреплённые chat assets через tools, отдельный Geometry Verifier возвращает решения, Geometry Inspector собирает валидированный JSON. Исходный присланный текст сохранён отдельно без изменений. Native factories и локальная синтетическая проверка не означают deployment, live Meta/Geometry/iPhone доступ или подтверждённую точность инспекции. Реальные схемы — в [discrepancy.py](../src/ai_inspector/discrepancy.py) и публичном GET /contracts каждого настроенного сервиса; этот guide описывает ответственность, запуск и ограничения.

Задание для coding agent, подготовленное по обсуждению. Термин «контракт» здесь означает правила обмена данными и ответственность компонентов.

**Дополнение 02.11.2026:** Inspector backend может серверно формировать этот вход из принятого Geometry compact v2 и автоматически продолжать явно запрошенную проверку. Настройка, поддерживаемые исходники, ограничения и API — в [Geometry integration](capture-to-geometry.md#downstream-discrepancy-review). Поиск/измерения остаются ответственностью Geometry; преобразование отчёта не сертифицирует их точность. Старый ручной путь передачи candidates/assets сохранён.

## 1. Цель

Организовать проверку предполагаемых несоответствий между IFC-моделью и фактической съёмкой через два отдельных агента. Geometry Inspector получает задание Bob, вызывает необходимые разрешённые tools и готовит пакет кандидатов/доказательств. Geometry Verifier оценивает этот пакет, определяет обоснованность каждого кандидата и возвращает структурированные решения Geometry Inspector. Geometry Inspector проверяет и собирает итоговый JSON-отчёт для Bob. Оформление отчёта для человека выполняет другой компонент.

Важно различать найденного кандидата и подтверждённую проблему. Сам факт появления кандидата в исходном JSON не означает, что строительное несоответствие действительно существует.

## 2. Подтверждённые решения

Уточнено владельцем после обсуждения:

1. **Bob — оркестратор и чат-агент:** общается с пользователем и вызывает инструменты либо конкретных агентов-инспекторов.
2. **Geometry Inspector — отдельный агент ведения проверки:** собирает evidence через tools, управляет шагами задачи, вызывает Geometry Verifier и формирует итоговый JSON для Bob.
3. **Geometry Verifier — отдельный аналитический агент:** оценивает готовых кандидатов и evidence, возвращает решения и запросы недостающих данных Geometry Inspector. Исходный поиск кандидатов выполняется вне этой аналитической проверки.
4. **Раздельные сервисы и состояние:** Geometry Inspector и Geometry Verifier имеют собственные instructions, permissions, bounded loop, history и task state; их масштабирование и развёртывание могут выполняться независимо.
5. **Межагентные данные структурированы:** задание, пакет evidence, решения проверки и итоговый отчёт имеют отдельные JSON-контракты. Оформление для человека выполняет другой компонент.

## 3. Роли и взаимодействие

Внешнее взаимодействие:

```text
Пользователь ↔ Bob
               ├─ вызывает инструменты и других инспекторов по своим задачам
               └─ передаёт задание Geometry Inspector
                    ├─ вызывает необходимые tools и получает evidence/измерения
                    ├─ передаёт пакет кандидатов Geometry Verifier
                    │    └─ получает JSON с решениями по каждому кандидату
                    └─ проверяет и собирает итоговый JSON → Bob

JSON с результатами → отдельный компонент оформления отчёта
```

- **Bob** — оркестратор и интерфейс общения с пользователем. Выбирает и вызывает нужные инструменты или конкретных инспекторов, передаёт им контекст и материалы, получает результаты и передаёт пользователю запросы недостающих данных.
- **Geometry Inspector** — ведёт предметную задачу: получает готовых кандидатов, выбирает необходимые tools, подготавливает evidence, делегирует анализ Geometry Verifier и возвращает Bob итоговый JSON. При запросе дополнительных материалов выполняет допустимый следующий шаг или передаёт запрос пользователю через Bob.
- **Geometry Verifier** — принимает подготовленный пакет, проверяет классификацию объектов и обоснованность несоответствий, возвращает Geometry Inspector все решения, объяснения, ссылки на evidence, ограничения и data requests. Он отвечает за предметный status кандидата; Geometry Inspector не переписывает принятое решение скрыто при сборке отчёта.
- **Компонент оформления отчёта** — использует JSON проверки для подготовки представления для человека. Его формат и реализация находятся вне данного контракта.

Минимальная целевая цепочка содержит три агентные роли: существующий Bob и два отдельных новых агента — Geometry Inspector и Geometry Verifier. Geometry, backend-валидация JSON и компонент оформления не добавляют обязательных агентных ролей. В будущем Geometry Inspector может направлять задачи разным зарегистрированным Geometry Verifier профилям; правила маршрутизации, fan-out и агрегации потребуют отдельного контракта. Разделение не означает неограниченного рекурсивного делегирования.

В обсуждении также упомянут агент сверки узлов. Это возможный другой профильный инспектор; его конкретная задача и контракт здесь не определены.

## 4. Три категории несоответствий

Это категории данных, а не последовательные этапы выполнения.

| Предлагаемый ключ JSON | Значение | Что требуется проверить |
| --- | --- | --- |
| `ifc_only` | Объект есть в IFC, но не обнаружен в съёмке | Подтверждают ли материалы отсутствие объекта; достаточно ли съёмки для такого вывода |
| `scan_only` | Объект обнаружен в съёмке, но отсутствует в IFC | Правильно ли распознан объект и обосновано ли отсутствие соответствия в модели |
| `geometry_mismatch` | Объект сопоставлен, но его положение или геометрия отличаются | Какое именно расхождение найдено и подтверждают ли его материалы и измерения |

Для `geometry_mismatch` различать:

- смещение объекта;
- отличие формы или размеров;
- оба типа расхождения одновременно.

Это уточнения внутри третьей категории, а не дополнительные основные категории.

Для каждого кандидата сохранять исходную причину попадания в проверку. В результате отдельно указывать основание решения агента и материалы, на которых оно основано. Под обоснованием понимается проверяемое объяснение вывода, а не внутренние рассуждения модели.

## 5. Входные материалы

### 5.1. Общие материалы проекта

Передаются как готовый результат предыдущей обработки и могут использоваться для разных случаев:

- построенное облако точек — общий файл;
- сегментация этого облака по категориям;
- доступная IFC-модель, если она уже есть в системе.

Обязательная повторная передача IFC Geometry Verifier в обсуждении не предусмотрена. Если для конкретного решения нужна информация из IFC, её можно передать в данных кандидата или предоставить доступ к уже имеющейся модели.

Не требуется строить облако точек или выполнять исходную сегментацию в рамках этого контракта.

### 5.2. Материалы отдельного случая

Каждый кандидат должен быть связан со своими материалами:

- категория несоответствия и причина его обнаружения;
- сведения об объекте и его местоположении;
- фотография или фотографии;
- соответствующий фрагмент сегментации либо ссылка на выделенную область общей сегментации;
- при наличии — дополнительная LiDAR-съёмка или локальный фрагмент облака точек;
- для геометрических расхождений — переданные измерения, направление и величина смещения, сравниваемые размеры.

Geometry Inspector выбирает необходимые tools и готовит полезные материалы из доступных. Geometry Verifier получает разрешённые превью/выдержки и вычисленные данные, а не только имена файлов. Дополнительная LiDAR-съёмка остаётся необязательным источником.

Визуализация геометрии может помогать объяснять расхождение, но создание 3D-модели не согласовано как обязательный результат.

## 6. Правила проверки

Geometry Inspector сначала собирает пакет и закрепляет его версии/источники. Затем Geometry Verifier выполняет следующие шаги:

1. Прочитать категорию кандидата и исходную причину его обнаружения.
2. Изучить связанные фотографии, сегментацию, измерения и другие доступные материалы пакета.
3. Проверить, подтверждается ли классификация объекта. Например, соответствует ли объект, размеченный как дверь, тому, что видно на фотографии.
4. Проверить само предполагаемое несоответствие в пределах доступных данных.
5. Зафиксировать решение, краткое обоснование и ссылки на использованные материалы.
6. Вернуть Geometry Inspector JSON с результатами проверки и запросами недостающих данных.

Geometry Inspector проверяет формат, case/source bindings и полноту результатов, собирает итоговый JSON и возвращает его Bob. При ошибке контракта он запрашивает исправленный результат; при нехватке evidence готовит новый связанный пакет. Финальный предметный status меняется только через новый принятый результат проверки, а не при форматировании JSON. Детерминированная проверка схемы и сериализация выполняются backend-кодом и не требуют дополнительного агента.

**Пример из обсуждения:** система создала кандидат «дверь найдена в съёмке, но отсутствует в IFC». На фотографии видно, что сегментированный объект не является дверью. Такой кандидат следует отклонить как ошибку сегментации, а не включать в подтверждённые строительные несоответствия.

**Неполные данные:** Geometry Verifier сохраняет неопределённость и описывает недостающее в `data_requests`. Geometry Inspector передаёт запрос пользователю через Bob. После появления новых данных создаётся новый связанный вызов с новым request_id и immutable пакетом. Недостаток данных не равнозначен ни подтверждению, ни отклонению кандидата.

## 7. Структура данных v1

Реализованные Pydantic models находятся в [discrepancy.py](../src/ai_inspector/discrepancy.py). GET `/contracts` возвращает JSON Schema конкретного сервиса; native operator API также включает InspectionRequest в OpenAPI. Неизвестные поля, дубли JSON members в A2A/result и нечисловые/бесконечные величины не принимаются. Не менять wire schema и profile responsibilities под уже закреплённой версией.

### Межагентные сообщения

| Направление | Model / kind | Содержание |
|---|---|---|
| Bob → Geometry Inspector | `InspectionRequest` / `inspection_request` | request_id, project_id, task, source bindings, assets и cases |
| Geometry Inspector → Geometry Verifier | `EvidencePackage` / `evidence_package` | inspection, request_sha256 и полный список прочитанных evidence, включая явные ограничения непросматриваемых assets |
| Geometry Verifier → Geometry Inspector | `ReviewResult` / `review_result` | request_id, project_id, package_sha256, assessment_scope и cases |
| Geometry Inspector → Bob | `InspectionReport` / `inspection_report` | chat_id/task, request/package/review hashes, reviewer_task_id, source, неизменённые cases, case_context, assets/asset_status и выбранные переданные measurements |

Каждый payload имеет schema_version `1.0`. A2A остаётся служебной оболочкой с schema_version `0.1` domain extension. Hashes вычисляются по каноническому JSON нормализованной модели/замороженного запроса; не по временному URL. Request/operation/task IDs разных переходов не взаимозаменяемы.

### Входные данные

`InspectionRequest.assets` содержит asset_id, file_id, SHA-256 и kind: photo, segmentation, point_cloud, ifc, measurement или reference. file_id разрешается только в ранее загруженный доступный файл того же чата. Общий файл не копируется на каждый case.

Каждый кандидат имеет case_id, detection_reason, object_type, optional ifc_id/segment_id, location, evidence_ids, measurements, mismatch_types и reference_facts. Массивы `ifc_only`, `scan_only`, `geometry_mismatch` присутствуют явно. В одном запросе — 1–12 уникальных cases и 1–6 assets. Нет отдельного алгоритма поиска кандидатов: их передаёт предыдущая обработка.

`source` сохраняет при наличии session/manifest/Geometry job/revision/model/hash/segmentation bindings. Session-bound запрос проверяется против принятого источника текущего чата; workspace uploads остаются предоставленными пользователем материалами, а не автоматически доказанными кадрами этого capture. Без session нельзя объявлять capture/job/revision binding. origin различает operator_supplied, recorded, synthetic и geometry. Серверный импорт Geometry разрешён только с совпадающими chat/session/job/source/result-manifest bindings и hash файла; пользовательская загрузка не может назначить себе origin=geometry. IFC GUID с `$` сохраняются без потери символов. Для Geometry Engine geometry_revision означает ревизию приёмки Geometry Inspector, поскольку upstream не предоставляет свою ревизию. Оценка имеет scope `supplied_evidence_review`, а не сертификацию алгоритмов или всего проекта. Отдельные Inspector/verifier services должны использовать обновлённый контракт; при изменении опубликованного профиля повысить его версию согласно agent-chat.

`Measurement` содержит measurement_id, parameter, observed/expected, unit (m/rad), interval погрешности, acceptance_interval, extent, algorithm/version, source_asset_ids и при необходимости coordinate_frame_id/requires_alignment/alignment_verified. Последний флаг — переданное upstream свидетельство, не результат вычисления alignment моделью. Никакие допуски не назначаются по умолчанию.

Для подтверждения численного `geometry_mismatch` требуется полный target, известные interval/acceptance_interval, interval целиком вне допуска, нужная привязка и точное совпадение Measurement с прочитанным файлом `MeasurementEvidence`: schema_version=1.0, kind=measurement_evidence, measurements=[...]. JSON-файл закреплён по hash; проверяется целостность и структура переданных чисел, а не точность алгоритма их получения. Простой текст с числом или caller metadata сами по себе не заменяют этот численный источник. Полный JSON Schema MeasurementEvidence включён в схемы EvidencePackage.

`comparison_basis=visual_shape` допускает качественную проверку формы только с mismatch_types=[shape_or_dimensions], доступным фото и reference; measurement_ids в таком выводе пуст. Размеры и величина смещения из этой визуальной оценки не возникают. По умолчанию comparison_basis=metric.

### Выходные cases

Каждая строка содержит прежние case_id/detection_reason, status, decision_reason, evidence_used, measurement_ids, limitations и data_requests. Указываются только доступные evidence того же case и ID переданных измерений. Новые числа модель не возвращает: Geometry Inspector копирует выбранные исходные Measurement objects в report. Для needs_more_data обязательны limitation и конкретный запрос данных. Rejected cases сохраняются.

| status | Значение |
|---|---|
| confirmed | Материалы поддерживают конкретное заявленное несоответствие в согласованном scope |
| rejected | Конкретный кандидат отклонён с основанием, например ошибочная классификация |
| needs_more_data | Доказательств недостаточно для одного из этих выводов |

`ifc_only` confirmation требует reported expected-region observation и supporting reference evidence; `scan_only` — reported checked reference scope и supporting evidence. Эти исходные данные остаются предоставленными свидетельствами; система не объявляет покрытие/полноту IFC независимо вычисленными. Интервал, пересекающий границу допуска, видимый фрагмент и неизвестная uncertainty не подтверждают численное нарушение.

Пример ReviewResult (вымышленные IDs/hash, не реальная инспекция):

```json
{
  "schema_version": "1.0",
  "kind": "review_result",
  "request_id": "11111111-1111-4111-8111-111111111111",
  "project_id": "example-project",
  "package_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "assessment_scope": "supplied_evidence_review",
  "cases": {
    "ifc_only": [],
    "scan_only": [{
      "case_id": "case-001",
      "detection_reason": "Сегментация определила дверь; соответствие в IFC не найдено.",
      "status": "rejected",
      "decision_reason": "Предоставленное фото показывает ошибочную классификацию сегмента.",
      "evidence_used": ["photo-001", "segment-001"],
      "measurement_ids": [],
      "limitations": [],
      "data_requests": []
    }],
    "geometry_mismatch": []
  }
}
```

InspectionReport сохраняет эти решения, исходный source и proof bindings. case_context сохраняет заявленный object_type, ifc_id/segment_id, location, mismatch_types и исходные evidence_ids; это исходный контекст, а не скрытая переклассификация объекта. assets содержит scoped file IDs/hashes, asset_status — реально доступные/непросматриваемые материалы и ограничения. chat_id позволяет formatter разрешать ссылки через авторизованный file API. Поле measurements может содержать переданное измерение видимого фрагмента для диагностики; это не повышает его до подтверждённого размера целого объекта. Оформление в Markdown/PDF/HTML находится вне этих агентов.

## 8. Геометрические данные

Данные должны позволять объяснить, что именно отличается и на сколько:

- сравниваемые значения из модели и съёмки;
- единицы измерения;
- для смещения — величина и направление с понятной привязкой к системе координат;
- для изменения формы или размеров — конкретные отличающиеся параметры;
- источник измерений и ссылка на объект или область, к которым они относятся.

Не придумывать отсутствующие размеры, направление смещения или систему координат. Если этих сведений недостаточно для интерпретации, отметить ограничение и запросить уточнение.

В обсуждении не определены допустимые строительные отклонения и пороги принятия решений. Не вводить произвольные допуски. Если для проверки нужен порог, он должен приходить из требований проекта или оставаться явно не заданным.

## 9. Передача результата компоненту оформления

JSON проверки должен содержать достаточно сведений, чтобы другой компонент мог подготовить:

1. Проект и использованные исходные материалы.
2. Сводка по подтверждённым, отклонённым и требующим уточнения случаям.
3. Подтверждённые несоответствия, сгруппированные по трём категориям.
4. Для каждого подтверждённого случая: объект, место, причина обнаружения, вывод проверки, подтверждающие материалы и переданные геометрические измерения.
5. Отклонённые кандидаты с причиной отклонения.
6. Случаи с недостаточными данными и конкретные запросы дополнительных материалов.

Этот перечень описывает сведения, которые нужно сохранить в JSON. Создание читаемого отчёта и выбор его формата относятся к компоненту оформления.

## 10. Критерии приёмки контракта

- Представлены все три категории; геометрические подтипы остаются внутри `geometry_mismatch`.
- Для каждого входного случая есть прослеживаемый результат по тому же `case_id`.
- Общие материалы доступны без повторной передачи на каждый случай.
- Фотографии, сегменты и измерения связаны с соответствующими случаями.
- Ошибка распознавания объекта может привести к отклонению кандидата с объяснением.
- Нехватка данных сохраняется как отдельное состояние.
- Геометрические выводы содержат источник измерений, единицы и необходимую координатную привязку либо явно отмечают их отсутствие.
- Geometry Inspector собирает готовых кандидатов и evidence через tools; Geometry Verifier анализирует подготовленный пакет. Исходный поиск несоответствий находится вне этой аналитической проверки.
- Bob вызывает Geometry Inspector, Geometry Inspector вызывает отдельного Geometry Verifier; запросы пользователю проходят через Bob.
- Geometry Verifier возвращает JSON Geometry Inspector, Geometry Inspector возвращает валидированный итоговый JSON Bob и сохраняет решения rejected/needs_more_data без удаления.
- У Geometry Inspector и Geometry Verifier независимые instructions, permissions, bounded execution и task state; повтор/перезапуск каждого сервиса не создаёт дубликатов заданий или принятых результатов.
- Оформление отчёта выполняет другой компонент.

## 11. Границы этого задания

Документ организует контракт обмена и поведение проверки. Он не определяет алгоритм исходного поиска несоответствий, инструменты сегментации, конкретный стек, интерфейс просмотра облака или обязательную генерацию 3D-моделей. Эти решения нельзя считать согласованными по приведённому обсуждению.

## 12. Согласование со структурой Geometry Inspector

### 12.1. Место контракта и роли

В коде роль Bob соответствует существующему `OrchestratorAgent` ([agent.py](../src/ai_inspector/agent.py)); `orchestrator` остаётся его служебной идентичностью. Имя Bob не требует переименования Python-классов, сохранённых runs или A2A identities. Пользовательский диалог Bob не ограничивается JSON. Требование «только JSON» относится к предметному результату профильного инспектора.

Geometry Inspector и Geometry Verifier подключаются как разные versioned profiles/services на общем harness, каждый со своими instructions, permissions, bounded loop, историей и task store/worker. Bob ведёт пользовательский workflow, Geometry Inspector — предметный workflow сбора/проверки, Geometry Verifier — аналитическое решение. Существующий model adapter Meta/Muse Spark сохраняется; новый capture pipeline не создаётся.

Реализован только явно настроенный hop Geometry Inspector → зарегистрированный discrepancy_verifier. Generic specialists по-прежнему не могут делегировать; Geometry Verifier также не имеет tools/delegation. Endpoint, version и token reference выбирает конфигурация сервера. Concrete child operation, exact A2A body и package checkpoint сохраняются до IO; child state остаётся в собственном task store. Geometry Inspector освобождает lease между polls, не вызывает модель во время ожидания и принимает результат только с корректными task/context/request/source bindings. Изменение сохранённой регистрации удерживает unfinished task до восстановления.

Контракт начинается **после исходного поиска кандидатов**. Предыдущая обработка предоставляет `ifc_only`, `scan_only`, `geometry_mismatch` и связанные данные; её конкретный владелец и алгоритм этим документом не назначены. Наличие реконструкции/сегментации Geometry само по себе не означает, что поиск кандидатов или сравнение с IFC уже реализованы. Профиль не обязан повторно строить облако, сегментировать весь проект или искать дополнительные несоответствия вне переданного набора.

Существующий [capture → Geometry contract](capture-to-geometry.md), session/source/job ownership и recovery не заменяются этим документом. Не вводятся ранее предложенные report/comparison HTTP routes, обязательные reconstruction-only/element-checks profiles, дополнительные алгоритмы или допуски из старого черновика. Они не считаются принятыми автоматически.

### 12.2. Смысл решений

Три категории кандидатов независимы от трёх решений проверки. Геометрический подтип — смещение, форма/размеры или оба — остаётся внутри `geometry_mismatch`. Категория и исходный `detection_reason` сохраняются; объяснение инспектора записывается отдельно. Ошибочно присвоенную категорию нельзя незаметно переносить в другой массив: результат сохраняет исходный случай и явно объясняет обнаруженную ошибку.

`confirmed` подтверждает конкретное переданное несоответствие в пределах evidence и критериев. `rejected` отклоняет конкретного кандидата, например из-за ошибочной классификации; он не означает, что весь объект или проект соответствует требованиям. Пустой список кандидатов и отсутствие confirmed также не доказывают полноту проверки проекта.

Отдельный неопределённый исход соответствует первоначальному требованию владельца о недостаточных данных и досъёмке. `needs_more_data` и `data_requests` реализуют этот смысл в v1. Недостаток информации не превращается в rejected. Geometry Verifier возвращает запрос Geometry Inspector; Geometry Inspector получает разрешённые материалы через tools или передаёт запрос пользователю через Bob. Geometry Verifier не управляет камерой и не создаёт capture session; backend сохраняет ownership capture/Geometry.

Для `ifc_only` «не обнаружено» не доказывает физическое отсутствие: нужны достаточные наблюдения ожидаемого места, отсутствие закрывающей области и надёжная привязка. Для `scan_only` отсутствие соответствия должно относиться к проверенному объёму и версии IFC; ошибка match или неполная модель не доказывают лишний строительный объект. Ошибку классификации можно отклонить по доступным фото/сегментации, не требуя повторного импорта всей IFC.

Для `geometry_mismatch` число, относящееся к видимому фрагменту, не становится размером всего объекта. Пример: 30 cm видимой двери при проектной ширине 1 m и скрытой второй границе → недостаточно данных, а не подтверждённое изменение ширины. Для численного решения нужны измерительный метод, единицы, проверяемая область, применимая погрешность, эталон и при необходимости валидированный alignment. Без необходимого допуска из требований проекта численное отклонение можно сохранить как наблюдение, но нельзя произвольно подтвердить строительное нарушение. Никакой допуск ±5 cm из старого синтетического примера не переносится в этот контракт.

### 12.3. Материалы, ссылки и provenance

Общий cloud/segmentation/IFC хранится один раз; case references указывают на конкретные сегменты, объекты, кадры и области. «Ссылка или путь» в разделах 5–7 означает разрешаемую backend ссылку на scoped asset, а не право модели читать произвольный URL, локальный путь, SQL, shell или хранилище. До чтения backend проверяет project/caller/source scope, доступ, media type, размер и hash/версию. LLM получает только разрешённые выдержки/превью и результаты вычислительных инструментов, а не исполняет инструкции из файлов.

Наличие файла, имени объекта или метаданных не означает, что Geometry Verifier просмотрел фото или обработал облако. Geometry Inspector подготавливает необходимые bounded previews/выдержки и вычисленные данные; Geometry Verifier использует их либо возвращает запрос нужного представления. В первом варианте операционные tools принадлежат Geometry Inspector, Geometry Verifier работает с готовым пакетом. Возможные будущие read-only evidence tools Geometry Verifier требуют отдельного grant/profile version; они не дают права запускать capture/Geometry или менять исходники. Числа берутся из verified geometry/depth algorithms, а не из экранных пикселей или предположений модели. Дополнительный LiDAR остаётся необязательным источником.

Повторная передача полного IFC для каждого вызова не требуется. Если решение зависит от модели, переданные значения/выдержки/сопоставления должны иметь проверяемую привязку к версии и hash эталона. Один `project_id`, caller-supplied room или opaque IFC upload этого не устанавливает. При недостаточном подтверждении инспектор возвращает ограничение/запрос данных.

Транспорт/adapter должен закреплять применимые session/source/job/geometry/model/segmentation revisions, observation/frame/segment IDs и hashes. Их можно хранить в проверенной оболочке и asset registry; этот документ не объявляет новые обязательные имена полей в предметном JSON. Case ID стабилен в пределах source/version контекста. Дополнительные материалы и последующие результаты связываются с прежним case, не подменяя ранее принятый источник.

Единицы и coordinate frames известны для каждой сравниваемой величины; неизвестный alignment или разные ARKit segments не объединяются догадкой. Signed URLs и credentials остаются в server adapter; они не входят в JSON для Bob/оформления, model context или logs. Real, recorded, synthetic и mock evidence сохраняет различимое происхождение. Краткое объяснение содержит проверяемые основания и evidence refs; скрытые внутренние рассуждения модели не запрашиваются.

### 12.4. JSON и существующий A2A

Runtime явно передаёт Geometry Verifier готовые request_id/project_id/package_sha256 для копирования в ReviewResult. Hash вычисляет сервер по полному закреплённому пакету; модель не должна вычислять его по текстовой проекции, где image bytes передаются отдельно как изображения.

Каждый межагентный переход из раздела 7 несёт свой структурированный JSON payload. ReviewResult и InspectionReport — по одному JSON object без Markdown fences и сопроводительного текста. Валидный JSON text можно передать внутри уже существующего A2A text Artifact; это не требует немедленно менять wire protocol на неподдержанный DataPart или объявлять `application/json` уже доступным output mode. A2A envelope остаётся служебной оболочкой; Geometry Inspector принимает ReviewResult, Bob — InspectionReport. Инструменты возвращают свои согласованные результаты и не обязаны создавать полный InspectionReport.

Валидация выполняется **до принятия завершённого предметного отчёта** на проверяющем, Geometry Inspector и Bob: синтаксис JSON и v1 schema, все три массива, ровно один результат на каждый входной case ID, прежняя категория и detection_reason, допустимое решение, ссылки на действительно доступные case assets. Неизвестные/чужие/повторные/пропущенные случаи отклоняются как ошибка контракта. Неполный/слишком большой ответ нельзя тихо обрезать; limits и разбиение описаны в разделе 13.

Geometry Inspector сохраняет исходный пакет и принятый ReviewResult; его итоговый InspectionReport ссылается на этот результат и сохраняет все confirmed/rejected/неопределённые cases, explanation, evidence, limitations и data requests. Удаление лишних полей из проекции не изменяет исходные данные; отклонённые кандидаты остаются прослеживаемыми. При несогласованности Geometry Inspector отклоняет результат как ошибку контракта или запрашивает новую проверку, а не переписывает status самостоятельно. Компонент оформления также не добавляет измерения и не меняет принятый status. JSON от модели сам по себе не становится независимо проверенным численным измерением или сертификацией compliance.

Техническая ошибка модели, доступа к asset или JSON parsing — ошибка исполнения/контракта. Она не маскируется под needs_more_data для объекта и не порождает просьбу переснять его без фактического ограничения съёмки. Если один case завершён, а другой требует данных, результаты сохраняются раздельно; обзор не сводится к одному общему флагу.

### 12.5. Идентичность, повторы и досъёмка

Сохраняются A2A request/operation/task IDs и frozen body/hash каждого перехода. Bob хранит identity и принятый итог задания Geometry Inspector; Geometry Inspector владеет своим workflow и хранит identity, input hash и принятый результат задания Geometry Verifier. Активный checkpoint Geometry Verifier остаётся в его независимом task store, не копируется в parent и не зависит от живого процесса Geometry Inspector. Эта связь реализована специализированным Geometry Inspector client; generic specialists не получают такой доступ. Case ID не заменяет task/operation ID. Повтор транспортного запроса использует прежний ID и точное тело; неизвестный сетевой исход не создаёт второго задания. Старый/cancelled результат не применяется к изменённой source/model revision. Готовый JSON — evidence, а не разрешение нового side effect.

Ответ пользователя с дополнительными данными — новый связанный запрос с новыми request/operation IDs, прежним case context и новой зафиксированной версией evidence; тело старого запроса и завершённая история не переписываются. Текущий remote A2A не поддерживает интерактивные сообщения в уже существующую task, поэтому ожидание данных не имитируется таким сообщением. Успешный ReviewResult с неопределёнными cases может завершить текущую task Geometry Verifier. В v1 текущий пакет immutable: Geometry Inspector завершает одно задание итоговым JSON с data_requests, а Bob после новых материалов создаёт новое связанное задание Geometry Inspector и новый request_id. Автоматического обогащения прежней task новыми входами нет. Число таких итераций и ожидания ограничиваются policy/budgets, а не бесконечным обменом агентов. Реализованное Geometry-продолжение ограничено явно зарегистрированной проверкой принятого результата; общий механизм ожидания и применения дополнительных ответов пользователя остаётся дальнейшей работой.

Отмена task Geometry Inspector fence-ит его lease и принятие позднего результата; уже запущенная task Geometry Verifier может завершиться в своём store. Каскадная удалённая отмена не обещается; capture/Geometry она не отменяет.

Досъёмка использует существующий capture flow только по явному запросу пользователя. Открытая session продолжается; после sealing создаётся связанная новая session. Offline/restart не отменяет исходную session. Указанное инспектором `data_requests` само по себе не запускает камеру, upload или новый Geometry job.

### 12.6. Реализованная граница и оставшиеся зависимости

| Компонент | Состояние |
|---|---|
| Bob | Существующий OrchestratorAgent; typed request / выбранный candidate JSON, remote routing и проверка принятого report |
| Geometry Inspector | DiscrepancyInspectorService на shared harness, собственный SQLite task store/worker, inspection_evidence и review_candidates |
| Geometry Verifier | VerificationAgentService на shared harness и отдельном store/worker; JSON validation/repair, без tools и дальнейшего delegation |
| Evidence | Same-chat immutable upload refs/hash или серверный импорт принятого Geometry result, bounded image previews/text и typed supplied measurements; доступ через отдельный scoped backend token |
| Case outcomes | Проверка 1:1 case IDs/категорий/причин/доступных evidence, guards фрагмента/uncertainty/допуска, детерминированная сборка итогового JSON |
| Recovery | Frozen parent/child request, identical admission retry, независимый restart, polls без model turns, deadlines/attempt limits и cancellation fencing |
| Geometry/candidate discovery | Inspector-only adapter существующего /inspections API, закрепление evidence и сборка candidates из compact v2; нового IFC parser, детектора, измерительного или alignment алгоритма нет |
| Продолжение, форматирование и досъёмка | Явно запрошенная Geometry-проверка продолжается через очередь до общего ответа Bob; человеческий formatter и общий user-input workflow остаются дальнейшей работой |
| Live deployment/accuracy | Требуют отдельной настройки сервисов, live Meta/evidence integration и предметной приёмки |

Код: [agents](../src/ai_inspector/discrepancy_agents.py), [contracts](../src/ai_inspector/discrepancy.py), [evidence adapter](../src/ai_inspector/discrepancy_evidence.py), [shared harness](../src/ai_inspector/agent_runtime.py), [A2A service](../src/ai_inspector/a2a_service.py), [Bob](../src/ai_inspector/agent.py). Local fixtures не подтверждают строительную точность, реальную SAM/Geometry/Meta доступность или deployment.

## 13. Настройка и использование

### Native services

Два сервиса используют разные persistent INSPECTOR_AGENT_DATA_DIR и inbound secrets. Из ai_inspector в отдельных настроенных процессах:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.discrepancy_agents:create_verifier_app_from_env --factory --host 127.0.0.1 --port 8102
.\.venv\Scripts\python.exe -B -m uvicorn ai_inspector.discrepancy_agents:create_inspector_app_from_env --factory --host 127.0.0.1 --port 8101
```

Для обоих процессов задать INSPECTOR_AGENT_PUBLIC_URL, INSPECTOR_AGENT_DATA_DIR, INSPECTOR_AGENT_CALLERS_JSON и существующие INSPECTOR_META_BASE_URL/MODEL/API_KEY/TIMEOUT_S. Public URL в production — HTTPS. INSPECTOR_AGENT_PROFILE_JSON опционален для этих двух factories: по умолчанию version=3, IDs discrepancy_inspector/discrepancy_verifier, правильные kind/instructions/tools/budgets. Для custom profile его kind должен совпадать с factory; изменение responsibilities требует новой version. Generic factory сохраняет прежний контракт.

INSPECTOR_AGENT_CALLERS_JSON — private array client_id/project_id/token. У Geometry Inspector caller — Bob; у Geometry Verifier caller — Geometry Inspector. Token values хранятся в Secrets, не в документах или model payload. Они различаются между собой, backend-tool, operator/device/model credentials. Приложение/телефон не получают эти keys.

Только Geometry Inspector дополнительно получает INSPECTOR_AGENT_BACKEND_URL, INSPECTOR_AGENT_BACKEND_TOKEN и INSPECTOR_VERIFIER_JSON. Его backend token совпадает со значением tool_token_env, выданным этому Geometry Inspector в registry Bob. Пример регистрации Geometry Verifier в Geometry Inspector:

```json
{
  "inspector_id": "discrepancy_verifier",
  "kind": "discrepancy_verifier",
  "version": "3",
  "description": "Geometry Verifier: review supplied discrepancy candidates",
  "base_url": "https://verifier.example",
  "token_env": "INSPECTOR_VERIFIER_TOKEN"
}
```

INSPECTOR_VERIFIER_TOKEN — отдельный Secret только у Geometry Inspector; его значение соответствует inbound caller token у Geometry Verifier. Geometry Verifier не получает backend-tool token и не может читать другие assets или делегировать дальше.

В INSPECTOR_REMOTE_AGENTS_JSON на Bob зарегистрировать только Geometry Inspector:

```json
[
  {
    "inspector_id": "discrepancy_inspector",
    "kind": "discrepancy_inspector",
    "version": "3",
    "description": "Geometry Inspector: collect evidence and obtain a Geometry Verifier review",
    "base_url": "https://inspector.example",
    "token_env": "INSPECTOR_TASK_TOKEN",
    "tool_token_env": "INSPECTOR_TOOL_TOKEN",
    "allowed_tools": ["inspection_evidence"]
  }
]
```

INSPECTOR_TASK_TOKEN соответствует inbound secret caller Bob на Geometry Inspector. INSPECTOR_TOOL_TOKEN — выдаваемый backend tool secret. Сам Geometry Verifier не регистрируется для прямого вызова Bob. URL example обозначают конфигурацию, а не опубликованные сервисы. Registry по умолчанию остаётся пуст; код не создаёт deployment/Secrets автоматически.

### Вход из чата и API

В operator POST `/v1/chats/{chat_id}/messages` добавлено optional поле inspection_request с InspectionRequest v1. Тело сообщения и typed request сохраняются до model/tool IO; changed body при прежнем message ID конфликтует. Отдельный request_id candidate batch также нельзя переиспользовать с изменённым содержимым в том же чате.

Через существующий Files UI можно загрузить компактный JSON с kind=inspection_request, затем выбрать его **и все указанные evidence files** для сообщения. Полный JSON должен помещаться в сохранённый нетранкированный text excerpt (обычно до 16k символов). Он разбирается/закрепляется, но сам не исполняет instructions и не принуждает Bob запускать проверку без задачи пользователя. Несколько batch files или непомеченные evidence требуют уточнения; JSON других типов остаётся обычным вложением.

Для больших batches передать typed поле через API или явно разбить scope. Вход ограничен 12 cases/6 assets, EvidencePackage — 1.6 MB, A2A — 2 MiB, полный report — 16000 символов; ничего не обрезается молча. Текстовый asset ограничен 128k bytes с excerpt до 12000 символов; opaque IFC/point clouds — metadata-only и требуют производных представлений.

Фото: source до 8 MB и 20 MP, полный preview до 768px и 190k JPEG bytes, EXIF transpose/resize помечаются; preview не используется как измерительная шкала. Для native preprocessing доступен optional extra `evidence` (Pillow); без него поддерживается только уже ограниченный JPEG, остальные изображения получают явное ограничение. При необходимости установить extra обычным setup процесса; в этой задаче использована уже имеющаяся Pillow. Подписанные storage URLs не отправляются моделям.

Geometry Inspector вызывает только inspection_evidence(asset_id) и review_candidates(). Для каждого asset сохраняется source ID/hash и реально доступное содержимое/ограничение. Новые capture assets нужно явно представить в разрешённом chat evidence; автоматический импорт frames/Geometry measurements из произвольной папки не реализован. Existing Supabase blob adapter используется для чтения закреплённых uploads, а не для поиска всей папки/bucket.

По умолчанию Geometry Inspector имеет 12 model turns, Geometry Verifier — 3 (до двух исправлений JSON в общем бюджете). Ожидание Geometry Verifier ограничено 120 polls или 10 минутами; оно не вызывает модель повторно. Транспортные transient retries используют прежние ID/body, действующую server credential reference и bounded retry budget. Готовый ReviewResult повторно не вычисляется. Technical failure/timeout/invalid JSON остаётся технической ошибкой, а не автоматическим запросом пересъёмки.

Принятый InspectionReport доступен оператору как поле inspection_report в chat run/history; Bob получает JSON как результат delegation и может объяснить его пользователю. Human formatter остаётся отдельным компонентом. Для следующих данных создать новый candidate request_id и сообщение; старые cases, evidence hashes и результаты остаются в истории.

### Названия и совместимость

Публичные имена двух агентов — **Geometry Inspector** и **Geometry Verifier**. Geometry Service остаётся отдельным вычислительным API. Служебные IDs/kind `discrepancy_inspector` и `discrepancy_verifier`, имена Python-классов/factories и переменных окружения сохранены: переименование не меняет маршрутизацию и идентификаторы задач. Карточки A2A показывают новые имена при прежних skill IDs.

Стандартные профили имеют version `3`: инструкции явно описывают Geometry compact 2.0 — единицы/знак, null, качество, processing tolerance отдельно от проектного допуска, частичные surface statistics и связанные RGB/mask/overlay. При первом запуске использовать version `3` в обеих регистрациях, как в примерах выше. Маппинг evidence и сохранение прежних reviews описаны в [Geometry guide](capture-to-geometry.md#compact-result-20--agent-evidence). Для уже начатых задач сохранить их прежний профиль/версию до завершения: смена регистрации не переписывает замороженные задачи и может удержать их до восстановления сохранённой конфигурации. Предметные JSON-схемы остаются версии `1.0`.


### Google Cloud deployment — 2 November 2026

Geometry Inspector and Geometry Verifier are hosted as separate containers on
the Geometry Engine VM, with separate persistent SQLite task volumes:

- Geometry Inspector: `https://35.205.36.182.sslip.io/geometry-inspector`
- Geometry Verifier: `https://35.205.36.182.sslip.io/geometry-verifier`

Append `/.well-known/agent-card.json`, `/contracts` or `/docs` for discovery and
schemas. Task endpoints require the caller's dedicated Bearer token plus
`A2A-Version: 1.0` and `A2A-Extensions: urn:ai-inspector:delegation:v1`. Credentials
are server-only and distinct for Bob→Inspector, Inspector→Verifier and the
Inspector→Bob evidence tool. The initial deployment used profile version `2`,
project `demo-building`. Current code defaults to version `3`; check both live
agent cards and both caller registrations before changing routing. The deployment
registry and VM environment are independently managed, so publishing code alone
does not synchronize their versions or prove a complete inspection run.

The Cloudflare deployment registry includes only Geometry Inspector, with the
`inspection_evidence` tool grant. `INSPECTOR_TASK_TOKEN` and `INSPECTOR_TOOL_TOKEN`
are Workers Secrets; the verifier token is held only by the two specialist
services. Do not put credentials in this guide or the registry.

The VM polls `feat/ai-inspector-development` every two minutes for these agents;
Geometry Engine separately follows `geometry-engine`. Deployment configuration
is managed on the VM, outside this branch. The IP-based hostname is provisional:
check it after a VM stop/start. Hosting does not certify reconstruction accuracy
or establish a complete live inspection run.
