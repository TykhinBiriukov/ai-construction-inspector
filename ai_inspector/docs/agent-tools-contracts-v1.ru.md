# Контракты агента и сервисов: предлагаемая v1

Дата: 28 октября 2026. Читать вместе с [overview](project-overview-v1.ru.md) и [исследованием/планом](research-and-implementation-v1.ru.md).

**Статус:** проект контрактов. «Есть» ниже означает наличие операции в текущем Inspector backend. «Добавить» означает предлагаемую операцию, которую ещё нужно реализовать. Наличие adapter не означает, что внешний сервис уже поддерживает контракт.

## 1. Общие правила и идентификаторы

Внешний транспорт — HTTPS, телефонный канал событий — WSS. JSON — UTF-8, даты — UTC RFC 3339. Существующие payload используют schema_version=0.1; новые API ниже также предлагаются как 0.1. Несовместимое изменение выпущенной схемы потребует новой версии.

| Поле | Кто создаёт | Для чего |
|---|---|---|
| chat_id | Backend | История переписки |
| message_id | Backend | Конкретное сохранённое сообщение |
| request_id | Клиент | Повтор одного входящего запроса |
| file_id | Backend intake | Ссылка на зарегистрированные байты вложения |
| inspection_id | Backend | Контекст задачи |
| session_id | Session Service | Один набор съёмки |
| agent_run_id | Harness | Один ход обработки входного события |
| tool_call_id | Модель/API | Связь ответа tool с запросом модели; не ключ идемпотентности продукта |
| operation_id | Runtime, а для device-команд — iPhone | Повтор конкретной предметной операции |
| segment_id / observation_id | iPhone | Координатный сегмент / наблюдение |
| completion_id | iPhone | Повтор отправки финального manifest |
| job_id / operation_key | Backend | Одно задание Geometry и его доставка |
| event_id / cursor | Backend | Дедупликация / продолжение истории событий |

Project, пользователь и device scopes берутся из аутентифицированного контекста. Модель не может расширить их аргументами tool. Доступ проверяется и к самому ресурсу, и к каждому file reference. В текущем тестовом backend есть отдельные bearer identities operator/device/Geometry; полноценная пользовательская авторизация веба ещё нужна.

## 2. WebUI → harness

### 2.1. Новые операции

| Операция | Назначение | Статус |
|---|---|---|
| POST /v1/chats | Создать переписку | Добавить |
| POST /v1/attachments | Зарегистрировать вложение, вернуть file_id | Добавить |
| POST /v1/chats/{chat_id}/messages | Принять текст и ссылки на вложения | Добавить |
| GET /v1/chats/{chat_id}/messages | Восстановить историю с пагинацией | Добавить |
| GET /v1/chats/{chat_id}/snapshot | Текущее состояние чата/связанных сессий и resume_cursor | Добавить |
| GET /v1/chats/{chat_id}/events | Сохраняемые события с after/limit, резервный polling | Добавить |
| GET /v1/chats/{chat_id}/events/stream | SSE того же потока для браузера | Добавить |

Первый вариант attachments может использовать bounded multipart upload. При больших BIM-файлах backend выдаёт ограниченный upload plan и проверяет наличие/размер/hash перед переводом file_id в ready. Приём файла не объявляет его валидным BIM.

Пример запроса сообщения; UUID и названия здесь иллюстративные:

```json
{
  "schema_version": "0.1",
  "request_id": "9ee12906-e658-4d94-b477-d4540237a120",
  "text": "Начните съёмку комнаты 204 по приложенной модели.",
  "attachment_ids": ["file-demo-ifc"],
  "session_id": null
}
```

text обязателен и непустой. session_id=null означает новый запрос без привязки к существующей съёмке; переданный ID должен относиться к этому чату/проекту. Если у чата несколько открытых сессий и связь неоднозначна, runtime/агент выясняет адресата команды. На входе не требуется выбирать room_id до анализа модели.

**Принятие:** backend в одной транзакции сохраняет сообщение и событие запуска агента. Возвращает 202 с message_id, agent_run_id и status=queued. Это подтверждение приёма, а не готовый ответ. Успешный повтор request_id/body возвращает 200 со ссылкой на ту же операцию и её актуальным состоянием; изменение body под тем же ID — 409.

### 2.2. Ответы и прогресс

Канонический ответ агента сохраняется как сообщение. Прогресс передаётся отдельными событиями: agent.run.updated, session.updated, capture.progress.updated, message.created, geometry.updated. Временная потоковая генерация текста возможна позже; она не должна заменять сохранённый финальный ответ.

SSE и polling используют одну историю. WebUI сохраняет cursor после применения данных; при перезагрузке берёт snapshot/history, затем продолжает события. Snapshot и resume_cursor снимаются согласованно, без потери событий на границе. Нативный EventSource удобно использовать с same-origin веб-backend и cookie-сессией; bearer для служебного Inspector не помещается в публичную сборку или URL.

SSE определяет reconnect и Last-Event-ID, но хранение и replay реализует наш backend. Это архитектурный выбор для веба, а не свойство Llama API. [Стандарт WHATWG](https://html.spec.whatwg.org/multipage/server-sent-events.html).

## 3. Harness ↔ Meta / Llama

Backend использует адаптер ModelClient, скрывающий конкретный SDK. На входе: системная инструкция, сохранённая история/краткий контекст, текущие session snapshots и разрешённые tools с JSON Schema. На выходе: текст либо tool calls.

Цикл одного хода:

1. Сохранить входящее событие и взять lease на обработку. Для одной session изменения сериализуются; команды проверяют expected_revision.
2. Восстановить контекст из базы и вызвать модель.
3. Сохранить ответ модели и каждый предложенный вызов до исполнения.
4. Проверить имя tool, JSON, схему, права, лимиты и допустимое состояние.
5. Для изменения состояния создать/найти устойчивую operation, выполнить её и сохранить результат.
6. Передать модели результат с соответствующим tool_call_id, если нужен следующий шаг или объяснение.
7. Сохранить итоговое сообщение или статус ожидания и завершить ход. Ограничить число итераций, время и бюджет токенов.

Журнал tool operations содержит operation_id, trigger_event_id, agent_run_id, tool name, проверенные аргументы, status и result/error. Повтор обработки того же trigger восстанавливает сохранённый вызов, а не просит модель заново придумать побочное действие. Если результат внешнего запроса неизвестен после timeout, adapter сначала повторяет/сверяет ту же операцию.

Для ожидания телефона сохраняются session ID и условие продолжения. Значимые события — новое сообщение, проблема устройства, принятый source, готовый/ошибочный Geometry result. Каждый upload/heartbeat не запускает модель. Сообщения самого агента не создают бесконечный цикл новых ходов.

Официальный SDK описывает tools, tool_choice и JSON Schema аргументов. Наличие типов не подтверждает доступность конкретной модели в нашем аккаунте; это проверяется отдельным live spike. [Схема запроса SDK Meta](https://github.com/meta-llama/llama-api-python/blob/main/src/llama_api_client/types/chat/completion_create_params.py). SDK также прямо требует проверять сгенерированные аргументы перед исполнением. [Схема ответа SDK Meta](https://github.com/meta-llama/llama-api-python/blob/main/src/llama_api_client/types/completion_message.py).

## 4. Минимальный каталог tools

Имена с подчёркиваниями пригодны для регистрации в Llama. Все ниже — предлагаемая оболочка для агента; готовые HTTP endpoints сами по себе пока не являются зарегистрированными agent tools.

| Tool | Аргументы модели | Результат | Основа |
|---|---|---|---|
| bim_prepare | file_id, room_selector при необходимости, purpose | BIM job/status, model_version, rooms или room_context, warnings | Добавить BIM adapter |
| bim_get_job | bim_job_id | Состояние подготовки и результат/ошибка | Добавить для долгих BIM операций |
| session_create | device_id, capture_goal, room_context_ref либо явный capture-only context | inspection_id, session_id, состояния, storage readiness | Адаптировать существующие сервисные методы |
| session_get | session_id | Текущая session, source, Geometry job, ошибка | Есть GET session |
| session_cancel | session_id, reason | Квитанция локальной отмены, статус remote cancel | Добавить |
| capture_instruction_send | session_id, text, необязательный template_id | instruction/message ID и revision | Добавить |
| chat_message_publish | text, необязательная ссылка на session | message ID | Добавить; chat адресует runtime |
| geometry_ensure_job | session_id | Существующий или созданный job ID, статус | Общая операция с текущим worker |
| geometry_get | session_id | Статус job, geometry_result или ошибка | Проекция GET session |

Runtime сам подставляет principal, project/chat scopes и operation_id. Для изменения существующего ресурса он также подставляет expected_revision, когда команда зависит от его текущего состояния. Произвольный shell, SQL, Python-код, HTTP URL и секреты аргументами модели не принимаются. Адреса сервисов задаются конфигурацией backend.

Успешный tool возвращает компактный result с references; не возвращает все байты BIM/фото. Ошибка имеет code, message, retryable, details. Для асинхронной работы result содержит job ID и status=queued/running, завершение приходит событием или через status tool. Модель не должна удерживать вызов до окончания долгой реконструкции.

### 4.1. BIM-подготовка

purpose различает validate_model, select_room и prepare_capture_context. room_selector — устойчивый room ID либо текст для поиска. Разбор названия не даёт модели права выдумать IFC GlobalId.

Порядок внутри BIM Service:

1. Проверить принадлежность file_id, доступность байтов, фактический формат и разрешённую схему.
2. Выполнить parsing/структурную валидацию и извлечь единицы/координатные соглашения.
3. Если нужна комната, найти реальные кандидаты. При нуле кандидатов вернуть room_not_found, при нескольких — room_ambiguous и список для уточнения.
4. Подготовить контекст: идентификатор комнаты, границы/связанные элементы по определённому алгоритму и ссылку на подготовленные данные.

Смысл результата room_context:

```json
{
  "model_version": "normalized-model-demo-v1",
  "room_id": "IFC_SPACE_GLOBAL_ID",
  "source_file_id": "file-demo-ifc",
  "normalized_model_ref": "model-package-demo-v1",
  "room_package_ref": "room-package-demo-v1",
  "units": "m",
  "coordinate_convention": "BIM_PROVIDER_DEFINED_V1",
  "selection_method": "space_boundaries",
  "warnings": []
}
```

Это смысловой пример, не утверждённая схема конкретного BIM-провайдера. Поле coordinate_convention должно получить согласованное значение. Комната как IfcSpace, окружающие стены/двери и физически обрезанный IFC — разные результаты. Для v1 предлагается room package с сохранёнными source element IDs; алгоритм отбора и формат package нужно согласовать. Успешный parsing не равен проверке строительного соответствия.

Существующий upload-sha256 идентифицирует исходные байты и не является версией нормализованной модели. Новый tool не меняет model_version уже созданной session. Capture-only context явно не связан с BIM; нельзя использовать демонстрационный room-204 как реальную выбранную комнату без данных пользователя.

### 4.2. Создание сессии

session_create оборачивает создание inspection/context и session. В capture-only варианте использует существующий путь. В BIM-варианте нужно добавить создание inspection из проверенного model reference: нынешний POST /v1/inspections принимает обязательные bim_model и room_id и лишь сохраняет opaque bytes, поэтому пока не является таким контрактом.

Создание session, assignment и receipt сохраняется до обращения к storage. При сбое подготовки prefix повтор той же операции продолжает прежнюю session. Телефон получает assignment только после готовности storage. Повтор session_create не меняет закреплённые model/room/device/policy.

## 5. iPhone ↔ Inspector

### 5.1. Существующий capture-контракт

| Операция | Смысл | Статус |
|---|---|---|
| GET /v1/devices/{device_id}/capture-assignments | Получить назначенные session IDs | Есть |
| GET /v1/devices/{device_id}/events и WSS /v1/devices/{device_id}/events/ws | Snapshot/replay/live session events | Есть |
| POST /v1/sessions/{session_id}/acknowledgements | Подтвердить получение ID | Есть |
| POST /v1/sessions/{session_id}/capture-start | Подтвердить фактическое начало | Есть |
| POST /v1/sessions/{session_id}/upload-plans | Получить разрешения на точные файлы | Есть для Supabase |
| POST /v1/sessions/{session_id}/complete | Передать manifest reference и completion ID | Есть |
| GET /v1/sessions/{session_id} | Сверить состояние | Есть |

Точные тела и retry-правила: [iOS guide](ios-integration.md), [ZIP guide](zip-capture.md). Эти операции вызываются приложением, а не моделью от имени телефона. Соединение инициирует iPhone; публичный адрес или HTTP-сервер на телефоне не нужен.

### 5.2. Новые сообщения и инструкции

Предлагается POST /v1/sessions/{session_id}/messages для входящих сообщений operator/device и серверных agent messages. Автор и канал определяются по identity, а не по полю sender в body.

```json
{
  "schema_version": "0.1",
  "operation_id": "9058358d-cc86-4f4f-abaa-e45eed5d0486",
  "kind": "problem",
  "text": "Комната завалена, к стене не подойти.",
  "instruction_id": null
}
```

kind: comment | problem | instruction. Backend сохраняет сообщение и outbox event атомарно, отвечает 201 или 200 при replay. Сообщение телефона показывается в вебе как оригинал с авторством, даже если Llama недоступна. Для problem запускается ход агента; он может запросить уточнение, предложить доступный ракурс либо объяснить ограничение. Само problem не является автоматической отменой.

Текстовая инструкция передаётся через capture_instruction_send и сохраняемый instruction resource. Если нужны статусы выполнения задания, использовать pending/collecting/satisfied/cannot_complete/cancelled из более широкого контракта. Для минимальной v1 достаточно доставки текста и ответа с instruction ID; measured coverage этим не вводится.

Добавить GET /v1/sessions/{session_id}/messages с пагинацией для восстановления. Событие message.created доставляется в chat stream веба и в device stream назначенного телефона. Чтобы избежать дублей в UI, у всех представлений один message ID. Применение инструкции после sealed/cancelled отклоняется; обычный комментарий может сохраняться и после окончания.

Прогресс: предлагается POST /v1/sessions/{session_id}/capture-progress с operation_id, монотонным progress_revision и счётчиками recorded/uploaded/pending. Поздний меньший revision не откатывает UI. Эти счётчики — заявление клиента для интерфейса, не доказательство готовности source. Можно ограничить частоту и объединять обновления; Llama по ним не вызывается.

### 5.3. Восстановление и завершение

iPhone долговременно хранит session ID, закреплённый контекст, cursor, очередь операций, файлы, upload receipts и manifest bytes. При восстановлении: аутентифицироваться → snapshot/replay → сверить session state → повторить pending операции прежними ID → перевыпустить истёкшие upload permissions при необходимости.

Текущий device WebSocket передаёт session.assigned/session.updated и control ping/pong. Message/instruction/progress events требуют расширения сервера и приложения. Не следует отправлять текст проблемы в нынешний WS как произвольную бизнес-команду.

Последовательность окончания: зафиксировать capture set → догрузить все объекты → загрузить manifest → отправить complete → дождаться принятия source. HTTP 202 означает только долговременный приём completion. При отвергнутом наборе исправление оформляется новым completion ID/manifest, а обычный timeout повторяется со старыми байтами и ID.

## 6. Geometry tool и существующий worker

В текущем backend успешная проверка source атомарно создаёт Geometry job и delivery work item. Поэтому geometry_ensure_job должен использовать этот же механизм и возвращать уже существующий job, если он создан. При реализации реестра tools потребуется выделить общую сервисную операцию из workflow; дублирующую очередь рядом добавлять нельзя.

Предлагаемый инвариант: один initial job на session + accepted source + processing profile. Приём source выполняет это продолжение автоматически. Агент может вызвать tool для проверки/обеспечения запуска, но отсутствие повторного вызова Llama не блокирует штатный pipeline.

Если требуется отдельное решение агента после съёмки, понадобится новое состояние awaiting_geometry_decision и отключение текущего auto-dispatch. Для описанной v1 такая зависимость не нужна; используется заранее заданное продолжение.

Существующий Inspector → Geometry handoff:

```text
PUT {GEOMETRY_BASE_URL}/internal/v1/jobs/{job_id}
```

Кроме session_id, команда содержит job_id, operation_key, inspection/project/room/model/profile identifiers и source_ref: фиксированную служебную ссылку Inspector, manifest hash и snapshot/version identity. Это связывает задание с конкретными байтами, а не с меняющейся «папкой сессии».

Geometry читает GET /internal/v1/sessions/{session_id}/capture-source и страницы /capture-source/files под service identity, получает ограниченные ссылки и скачивает принятые файлы. Статус проверяется по GET того же job; уведомления принимаются в POST /internal/v1/geometry-events. Ссылки на скачивание можно обновлять без изменения принятого источника.

| Условие | Действие |
|---|---|
| Capture ещё открыт или source не принят | source_not_ready, job не запускается |
| Уже есть initial job | Вернуть его ID/состояние |
| Timeout при отправке | Повторить тот же job/operation key/body |
| Callback ready | Сохранить inbox, сверить job/source/version; получить проверяемое состояние job |
| Geometry failed | Сохранить ошибку, не открывать sealed capture |
| Поздний результат после отмены | Сохранить диагностический факт, не активировать отменённый workflow |

Отдельная повторная реконструкция с новым профилем или намеренная повторная попытка после terminal failure — будущая явно именованная операция, не сетевой retry.

## 7. Отмена, потеря связи и aborted

**Потеря связи:** только device offline/last_seen. Открытая сессия остаётся в базе. На языке продукта пользователь видит «Нет связи, сессия сохранена».

**Остановка хода модели:** отменяет конкретный agent_run, но сама по себе не отменяет съёмку или Geometry job. Поэтому aborted не используется как общий статус всех подсистем.

**Явная отмена сессии:** добавить POST /v1/sessions/{session_id}/cancel с operation_id, expected_revision и reason. Разрешить назначенному устройству и оператору согласно policy; агент вызывает эту операцию только в рамках прав инициатора. Повтор возвращает ту же квитанцию; конфликт состояния — 409.

- До sealed: закрыть capture как cancelled, остановить новые команды/выдачу upload grants и поставить processing_state=cancelled.
- После sealed, пока обработка активна: сохранить capture_state=sealed, отменить локальную обработку, отправить Geometry cancel только при наличии согласованного remote API.
- Уже completed/failed: не переписывать исторический результат в cancelled; вернуть понятное terminal-state решение.
- Ранее выданные signed URLs могут действовать до истечения. Даже если файл физически догрузится, отменённый source не принимается и новый job не создаётся.
- Remote cancel асинхронный: квитанция различает локальную отмену и remote_cancel_status=pending/confirmed/unsupported. Если Geometry не умеет отменять работу, локальная отмена всё равно блокирует применение позднего результата.
- Физическое удаление данных не входит в cancel; оно регулируется retention.

Отмена, приём source и применение результата используют транзакционную проверку состояния/revision. Побеждает первая принятая операция, следующая получает актуальное состояние; просроченный worker lease не может переписать его.

## 8. Сохраняемые события и данные

Пример нового события; identifiers условные:

```json
{
  "schema_version": "0.1",
  "event_id": "event-demo-17",
  "type": "message.created",
  "session_id": "session-demo",
  "chat_id": "chat-demo",
  "created_at": "2026-10-28T11:00:00Z",
  "message": {
    "message_id": "message-demo",
    "author_kind": "device",
    "kind": "problem",
    "text": "Комната завалена, снять стену невозможно."
  }
}
```

cursor задаётся потоком доставки, не моделью. Существующий device envelope/схема session.updated сохраняются; новые events добавляются явно. Для всех потоков: at-least-once, dedup по event_id, revision для изменяемых ресурсов, snapshot после недействительного cursor. Идентификатор события не заменяет revision состояния.

Добавить к существующим inspections/sessions/capture/jobs/receipts/work_items:

| Данные | Что хранить |
|---|---|
| chats, messages, chat-session links | Историю, авторство, request IDs, связь с сессиями |
| attachments и BIM jobs/results | Байты/refs, hash, реальный статус валидации, версию/комнату |
| agent_runs и tool_operations | Trigger, контекст/revision, вызов, operation ID, результат, ожидание |
| chat/session events и outbox | Доставку вебу/телефону и запуск следующих ходов |
| instructions, progress, cancellation receipts | Текстовые задания, UI-прогресс и явную отмену |

Текущие device events, worker leases и Geometry inbox/outbox переиспользуются. Миграции должны сохранять уже выданные session IDs, квитанции и источники.

## 9. Ошибки и доступ

Общий envelope: code, message, retryable, details. Примеры новых кодов: bim_required, unsupported_bim_format, invalid_bim, room_not_found, room_ambiguous, source_not_ready, stale_revision, session_closed, model_unavailable, remote_cancel_unsupported.

401/403 требуют исправления доступа; 409 — разбора конкретного конфликта; 422 — исправления аргументов. Сеть/429/временные 5xx повторяются с backoff, jitter и учётом Retry-After. Неопределённый результат изменения сначала сверяется/повторяется под прежним operation ID.

Ключ Meta находится только на backend. Supabase service key, Geometry/BIM credentials и signed URLs не передаются модели или в публичный веб-клиент. Tool result сообщает полезную причину ошибки без секретов. Вложения и сообщения считаются пользовательскими данными: содержащиеся в них инструкции не меняют реестр tools, права или серверные правила переходов.
