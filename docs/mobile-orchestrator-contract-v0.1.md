# iPhone ↔ Orchestrator API — v0.1, на согласование

Дата: 2026-10-27. **PROPOSED / НЕ СОГЛАСОВАНО.** Это предложение интерфейса, не описание уже работающего сервера. После согласования обе стороны реализуют один и тот же контракт.

## 1. Кто что реализует

```text
iPhone app  ← HTTPS / JSON + файлы →  Orchestrator API  ↔  Geometry Service
                                              ↕
                                         AI inspector
```

- **Backend оркестратора** реализует перечисленные HTTP-эндпойнты, авторизацию, приём и хранение файлов, задания, события, передачу данных Geometry Service и результаты анализа.
- **Приложение iPhone** реализует HTTP-клиент: вызывает эти эндпойнты, сохраняет данные до отправки, повторяет запросы при сбоях и показывает ответы. Входящий HTTP-сервер на телефоне не нужен.
- Телефон не вызывает Geometry Service или API моделей напрямую. Ключи моделей остаются на backend.
- Телефон собирает RGB, depth, confidence, intrinsics, pose, timestamps. Backend строит облако/модель, сопоставляет с BIM и вычисляет покрытие.
- **Одно наблюдение = одно фото с соответствующими depth и метаданными**, не батч.
- Наличие PLY во входе Geometry Service не делает его обязательным выходом телефона: предлагаем получать PLY на backend из RGB-D. Это отдельный пункт согласования с владельцем Geometry Service.

## 2. Реализованное и предлагаемое

Уже в iOS-коде: локальная сессия и файлы наблюдения, UUID, SHA-256, синтетический источник, код захвата ARKit. Формат записывается в [`Observation.swift`](../ios/AIInspector/Observation.swift).

**Не реализовано:** сетевой клиент, загрузка на сервер, авторизация, получение событий, BIM import, backend alignment/coverage, голос. Локальная демоинструкция не является ответом AI. Получение реальных RGB-D и их геометрическая корректность ещё не проверены на iPhone.

Документ ниже сохраняет существующий формат `frame.json`. Новые сетевые оболочки (project_id, app_version, ревизии, квитанции, состояние загрузки) требуют реализации. Существующий `SessionManifest` не нужно автоматически считать полным HTTP-запросом.

## 3. Общие правила

- Базовый URL предоставляет backend-разработчик. Все маршруты ниже относительно него. Для удалённого сервера — HTTPS.
- `Authorization: Bearer <token>`. Способ выдачи/обновления токена согласовать; токен ограничен проектом, на iPhone хранится в Keychain, не в репозитории.
- JSON UTF-8; даты UTC RFC 3339. `timestamp_s` кадра — монотонное время ARKit, **не UTC**.
- UUID генерирует клиент для session, segment, observation, response и completion. Передавать один и тот же строковый UUID во всех ссылках и повторах. ID проекта/комнаты/модели выдаёт backend; ID инструкций/событий — оркестратор.
- `schema_version: "0.1"`. Неизвестную версию отклонять с 422. Добавление необязательных полей допустимо; изменение единиц/осей/значения существующих полей требует новой версии.
- Ошибки и квитанции — JSON. HTTP 2xx не означает успешную инспекцию.
- Для MVP один телефон пишет в одну сессию. Несколько телефонов — отдельные сессии.
- Лимиты размера наблюдения, числа наблюдений и частоты запросов согласовать перед интеграцией. Не обрезать изображения или depth молча.

## 4. Сценарий MVP

1. Получить подготовленную модель комнаты.
2. Зарегистрировать сессию и координатный сегмент.
3. Снять наблюдение, сохранить весь набор на телефон, отправить оркестратору.
4. Получить квитанцию о долговременном сохранении. Только тогда увеличить счётчик «Отправлено».
5. Периодически запрашивать события. Получить текст «Сними проём с другого ракурса».
6. Снять дополнительное наблюдение с `instruction_id`, дождаться его приёма, отправить ответ с его ID.
7. Оркестратор решает, достаточно ли данных. Нажатие кнопки/загрузка кадра сами по себе не закрывают задание.
8. После цикла досъёмки завершить сессию с точным списком наблюдений. Проверять обработку отдельно от загрузки.

## 5. Маршруты

| Метод и путь | Назначение | Успех |
|---|---|---|
| `GET /v1/projects/{project_id}/models/{model_version}` | Подготовленная модель и BIM ID | 200 |
| `PUT /v1/sessions/{s}` | Создание сессии | 201 / 200 повтор |
| `PUT /v1/sessions/{s}/segments/{g}` | Регистрация системы координат сегмента | 201 / 200 повтор |
| `PUT /v1/sessions/{s}/observations/{o}` | Одно наблюдение, multipart | 201 / 200 повтор |
| `GET /v1/sessions/{s}/uploads?after={cursor}&limit=100` | Сверка принятых наблюдений | 200 |
| `GET /v1/sessions/{s}/events?after={cursor}&limit=100` | Задания и обновления | 200 |
| `GET /v1/sessions/{s}/instructions` | Актуальный снимок заданий и курсор | 200 |
| `POST /v1/sessions/{s}/instructions/{i}/responses` | Ответ пользователя | 201 / 200 повтор |
| `POST /v1/sessions/{s}/complete` | Закрытие приёма новых наблюдений | 202 / 200 повтор |
| `GET /v1/sessions/{s}` | Текущее состояние и результаты | 200 |

`s` = session_id, `g` = segment_id, `o` = observation_id, `i` = instruction_id. GET курсоры непрозрачные строки. Примеры ниже схематические: значения `SESSION_UUID` и подобные заменяются реальными ID; нулевые хеши — placeholders, не тестовые файлы для приёма сервером.

### 5.1 Модель для отображения

Ответ GET model:

```json
{
  "schema_version": "0.1",
  "project_id": "demo-building",
  "model_version": "demo-room-v1",
  "room_ids": ["room-204"],
  "is_synthetic": true,
  "units": "m",
  "axes": "right_handed_y_up",
  "asset": {
    "url": "/v1/assets/demo-room-v1.usdz",
    "media_type": "model/vnd.usdz+zip",
    "byte_length": 123456,
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "T_model_asset": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "elements": [
    {"element_id":"door-01","ifc_guid":null,"room_id":"room-204","node_name":"door-01","type":"door","label":"Doorway"}
  ]
}
```

Предлагаем USDZ, подготовленный backend/отдельным конвертером. Импорт произвольного RVT/IFC на телефоне не входит в MVP. `T_model_asset` переводит точки ассета в систему BIM-модели; метры и оси должны быть явно нормализованы. `node_name` должен реально существовать в ассете; если элемент состоит из частей, группировать их под этим узлом. Текущая генерируемая сцена приложения — отдельный демопример, ещё не этот пакет.

URL ассета — относительный путь на API или подписанный HTTPS URL. Bearer token приложения не пересылать стороннему хосту. Backend определяет срок действия подписанного URL; при истечении клиент повторяет GET model. `model_version` неизменяема; новые геометрия/ID требуют новой версии.

### 5.2 Сессия

PUT session, JSON:

```json
{
  "schema_version": "0.1",
  "session_id": "SESSION_UUID",
  "project_id": "demo-building",
  "room_id": "room-204",
  "model_version": "demo-room-v1",
  "model_is_synthetic": true,
  "capture_origin": "device",
  "input_mode": "live_capture",
  "created_at": "2026-10-27T10:00:00Z",
  "device": "iPhone",
  "os_version": "26.6.1",
  "app_version": "0.1.0",
  "parent_session_id": null
}
```

`capture_origin`: `device | synthetic`; `input_mode`: `live_capture | recorded_capture | synthetic_fixture`. Replay не меняет происхождение реальных данных на synthetic; оригинальные frame timestamps сохраняются. Одна сессия не смешивает источники. `model_is_synthetic` независимо от источника захвата: сейчас можно снимать телефоном при отображении синтетической комнаты, но это не подтверждённое сопоставление.

Квитанция: `{"session_id":"SESSION_UUID","state":"capturing","revision":1}`. ID пути и тела должны совпадать. Повтор с теми же полями — 200; изменение проекта, модели или остальных исходных полей с тем же ID — 409 `idempotency_conflict`. PUT не используется как произвольное обновление сессии.

### 5.3 Сегменты координат

PUT segment, JSON:

```json
{
  "schema_version": "0.1",
  "session_id": "SESSION_UUID",
  "segment_id": "SEGMENT_UUID",
  "created_at": "2026-10-27T10:00:01Z",
  "units": "m",
  "coordinate_convention": "arkit_world_camera_v1",
  "matrix_layout": "rows_column_vectors"
}
```

Ответ: `{"session_id":"SESSION_UUID","segment_id":"SEGMENT_UUID"}`. Регистрировать сегмент **до** первой загрузки его кадра. После перезапуска/сброса ARKit или непроверенной релокализации — новый segment_id. В текущем клиенте новый сегмент создаётся на каждом запуске камеры. Между сегментами нельзя предполагать общую систему координат. Геометрический backend связывает их отдельно, если сможет. Повтор с теми же значениями — 200, другой контент того же ID — 409.

### 5.4 Наблюдение и бинарные форматы

PUT observation: `Content-Type: multipart/form-data; boundary=...`.

| Имя части | filename | Content-Type | Обязательность |
|---|---|---|---|
| `metadata` | `frame.json` | `application/json` | Да |
| `rgb` | `rgb.jpg` | `image/jpeg` | Да |
| `depth` | `depth.f32` | `application/octet-stream` | Да |
| `confidence` | `confidence.u8` | `application/octet-stream` | Только если metadata.confidence != null |

Один набор получается из **одного ARFrame**. Независимо снятое фото нельзя соединять с произвольным кадром depth.

- RGB: JPEG исходного `capturedImage`, без поворота, crop, изменения размера и EXIF-поворота. Поворот экранного превью не меняет файлы и intrinsics.
- Depth: несглаженный `sceneDepth`, float32 little-endian, метры, row-major, без padding строк; ровно `width × height × 4` байт. Значения NaN/Infinity/≤0 сохраняются как есть, но исключаются из геометрии.
- Confidence: uint8 row-major, то же разрешение, ровно `width × height` байт; 0/1/2 = low/medium/high. Если нет карты — `confidence: null`, multipart-часть отсутствует.
- Нет depth/нормального tracking — клиент не формирует реальное геометрическое наблюдение. У синтетического кадра `tracking_state: synthetic`, `depth_source: synthetic_plane`.
- SHA-256 — lowercase hex от точных байтов файла. `byte_length` не включает multipart-заголовки. Имена файлов фиксированы, без произвольных путей.

Пример metadata, соответствует именам полей текущего Swift-кода:

```json
{
  "schema_version": "0.1",
  "observation_id": "OBSERVATION_UUID",
  "frame_id": "OBSERVATION_UUID",
  "session_id": "SESSION_UUID",
  "segment_id": "SEGMENT_UUID",
  "instruction_id": null,
  "capture_origin": "device",
  "timestamp_s": 15423.234,
  "saved_at": "2026-10-27T10:00:05Z",
  "rgb_size": [1920,1440],
  "depth_size": [256,192],
  "K_rgb": [[1400,0,960],[0,1400,720],[0,0,1]],
  "T_world_camera": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
  "tracking_state": "normal",
  "depth_source": "sceneDepth",
  "depth_unit": "m",
  "image_orientation": "native_buffer_unrotated_no_exif_rotation",
  "source_to_stored_image": [[1,0,0],[0,1,0],[0,0,1]],
  "depth_to_rgb_mapping": "Unrotated aligned buffers; pixel origin top-left, u right/v down. Scale K_rgb rows 0/1 by depthWidth/rgbWidth and depthHeight/rgbHeight. Device reprojection validation pending.",
  "rgb": {"name":"rgb.jpg","byte_length":123456,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
  "depth": {"name":"depth.f32","byte_length":196608,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"},
  "confidence": {"name":"confidence.u8","byte_length":49152,"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
}
```

Размеры, intrinsics и поза в примере условные, не результаты измерения. Backend проверяет JPEG dimensions, бинарные размеры, хеши, форму/конечность матриц, положительные focal lengths, валидность rigid transform и принадлежность segment/session. Для v0.1 `frame_id == observation_id`. `instruction_id` — контекст съёмки, а не доказательство принадлежности пикселей конкретному BIM-объекту. Поздняя загрузка кадра со старой инструкцией может сохраняться как evidence; переход состояния задания контролируется отдельным ответом с revision.

Квитанция возвращается **после долговременного сохранения и проверки всего набора**:

```json
{
  "session_id": "SESSION_UUID",
  "observation_id": "OBSERVATION_UUID",
  "status": "accepted",
  "metadata_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "accepted_at": "2026-10-27T10:00:06Z"
}
```

`metadata_sha256` считается по точным байтам multipart-части `metadata`. Клиент повторно отправляет сохранённый frame.json, не генерирует новые даты/форматирование. Multipart boundary может меняться. Одинаковые ID и metadata/file hashes → 200; изменённый набор с тем же ID → 409. Частично полученный набор не виден в uploads/events как accepted.

### 5.5 Сверка загрузок

GET uploads: `{"items":[{"observation_id":"OBSERVATION_UUID","metadata_sha256":"HASH"}],"next_cursor":null}`.

Страницы отсортированы по серверному порядку приёма. `next_cursor: null` означает конец текущего списка. Клиент сверяет ID **и** metadata hash; совпадение одного ID недостаточно. Сетевой timeout после PUT не доказывает потерю записи: повторить тот же PUT или свериться с uploads. Изменять ID для повтора нельзя.

### 5.6 События и инструкции

Для MVP — foreground polling; предлагаем интервал 2 секунды при активном экране. При ошибках backoff, без параллельных polling-запросов. Фоновый push/WebSocket не требуется в первой интеграции.

Первый GET events без `after` возвращает события с начала доступной истории. Ответ:

```json
{
  "events": [{
    "event_id": "evt-17",
    "type": "instruction.created",
    "session_id": "SESSION_UUID",
    "created_at": "2026-10-27T10:01:00Z",
    "instruction": {
      "instruction_id": "ins-3",
      "revision": 1,
      "session_id": "SESSION_UUID",
      "model_version": "demo-room-v1",
      "target_element_ids": ["door-01"],
      "text": "Снимите правый край проёма с другого ракурса.",
      "language": "ru",
      "state": "pending",
      "target_region": null,
      "audio": null
    }
  }],
  "next_cursor": "cursor-17",
  "has_more": false
}
```

- Доставка at-least-once; клиент дедуплицирует по event_id, сохраняет курсор **после** применения событий. При has_more продолжает немедленно, иначе ждёт следующий polling.
- Типы MVP: `instruction.created`, `instruction.updated`, `instruction.cancelled` — каждый содержит полный объект instruction; `session.updated` содержит объект состояния из GET session. Неизвестный тип можно пропустить с логированием, не ломая polling.
- `revision` инструкции монотонно растёт. Клиент не применяет старую/равную ревизию поверх новой. Состояния: `pending`, `collecting`, `satisfied`, `cannot_complete`, `cancelled`.
- При истечении курсора — 410 `cursor_expired`. GET instructions возвращает `{"instructions":[...],"resume_cursor":"..."}`: полный текущий снимок заданий, включая закрытые, и курсор одной атомарной серверной точки. Клиент заменяет локальный список, запрашивает GET session, продолжает events с resume_cursor.
- Для v0.1 `target_region` и `audio` всегда null. Их непустые схемы согласовать отдельно; текст остаётся основным представлением. Не получать пространственные координаты разбором текста.

POST response, JSON:

```json
{
  "schema_version": "0.1",
  "response_id": "RESPONSE_UUID",
  "instruction_revision": 1,
  "action": "observations_submitted",
  "text": null,
  "observation_ids": ["OBSERVATION_UUID"],
  "input_mode": "button",
  "audio": null
}
```

Действия: `acknowledged`, `observations_submitted`, `cannot_complete`, `message`. Для observations_submitted нужен непустой список **принятых** наблюдений этой сессии, связанных с instruction_id; для остальных — пустой список. Для message нужен непустой text; для cannot_complete text может содержать причину. MVP input_mode: `button | typed`; `voice` — будущее расширение.

Ответ: `{"response_id":"RESPONSE_UUID","status":"accepted"}`. Это приём ответа, не подтверждение достаточности evidence. При изменившейся instruction revision — 409 `stale_instruction` с актуальной инструкцией в details. При новой осознанной отправке после конфликта — новый response_id. Успешный повтор того же response_id/body возвращает исходную квитанцию **до** проверки текущей revision; другой body того же ID — 409.

### 5.7 Завершение и состояние

POST complete, JSON:

```json
{
  "schema_version": "0.1",
  "completion_id": "COMPLETION_UUID",
  "observations": [
    {"observation_id":"OBSERVATION_UUID","metadata_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
  ]
}
```

Перед запросом клиент останавливает захват, фиксирует inventory и дожидается всех квитанций. Список должен быть непустым, без дубликатов, **точно** совпадать с серверным набором ID/hash. Пропуски, лишние ID или несовпадающие хеши → 409 `inventory_mismatch` с соответствующими списками. Активные pending/collecting задания → 409 `instructions_pending`; сначала завершить цикл задания/досъёмки.

При успехе сервер атомарно закрывает приём новых ID и отвечает 202 с объектом состояния. Одинаковый completion_id/body → 200 без повторного запуска анализа; другой body того же ID → 409. Повтор уже принятого наблюдения остаётся допустимым после закрытия; новое наблюдение/сегмент → 409 `session_closed`.

GET session / session.updated payload:

```json
{
  "session_id": "SESSION_UUID",
  "revision": 8,
  "state": "processing",
  "accepted_observation_count": 12,
  "completion_id": "COMPLETION_UUID",
  "result": null,
  "error": null
}
```

Состояния серверной сессии: `capturing → processing → completed`, либо `failed` после ошибки обработки. Загрузка — отдельное клиентское состояние очереди, не результат инспекции. До complete сервер может анализировать evidence и выдавать задания, оставаясь в capturing. После complete новые задания съёмки не создаются в закрытой сессии. Если финальный анализ всё же требует досъёмки, result.outcome = needs_more_data; пользователь открывает новую сессию с parent_session_id, и оркестратор создаёт задания уже для неё.

Для completed поле result:

```json
{
  "outcome": "needs_more_data",
  "analysis_origin": "mock",
  "summary": "Недостаточно данных о правой стороне проёма.",
  "evidence_observation_ids": ["OBSERVATION_UUID"],
  "report_url": null
}
```

`outcome`: `issues_found | no_issues_detected | needs_more_data`; `analysis_origin`: `live_model | recorded_model | mock`. Evidence IDs должны принадлежать этой сессии. `no_issues_detected` означает результат текущего анализа, не сертифицированную строительную приёмку. `report_url` — nullable HTTPS URL отчёта, если он существует. Для failed поле error использует формат ниже; для остальных error = null. Клиент применяет только более новую revision состояния.

## 6. Координаты — обязательный пункт согласования

- Все JSON-матрицы — **массивы строк**; умножаются на векторы-столбцы. Swift SIMD хранит столбцы: сериализация в приложении явно переставляет индексы.
- `T_world_camera` — 4×4, **camera → world текущего segment**, не world → camera. Метры, правая система; world Y вверх, камера смотрит вдоль −Z, её X вправо, Y вверх.
- `K_rgb` — 3×3 intrinsics сохранённого исходного RGB; не экранного превью.
- Proposed pixel convention: начало сверху слева, u вправо, v вниз. `K_depth = diag(depthWidth/rgbWidth, depthHeight/rgbHeight, 1) × K_rgb`.
- Для пикселя (u,v) с depth d: `p_optical = d × inverse(K_depth) × [u,v,1]`; затем `p_camera = diag(1,-1,-1) × p_optical`; затем `p_world = T_world_camera × [p_camera,1]`.
- Это предлагаемое соглашение. Pixel-center convention, соответствие RGB/depth и знак осей **обязательно проверить на реальном наборе** до фиксации v1. Не считать синтетические тесты доказательством качества LiDAR.
- В backend точка переводится в BIM через отдельно вычисленное `T_bim_world`. Исходные poses и measurements не переписывать под BIM и не притягивать измеренные дефекты к проектным поверхностям.
- Поля `depth_to_rgb_mapping`/`coordinates` текущих локальных файлов пояснительные; сервер использует согласованную схему v0.1, а не парсит произвольный текст как алгоритм.

## 7. Alignment, покрытие и голос: следующий этап

Не блокируют первую передачу RGB-D и текстовый цикл. Непустые payloads для этих возможностей **пока не согласованы**.

Для будущего alignment нужны минимум model_version, segment_id, alignment_id, revision, status и T_bim_world. Для покрытия — model_version, revision, ссылки на evidence и явное отличие unknown от измеренного. До согласования/получения валидных данных телефон показывает модель отдельно, без позиции камеры, процента покрытия и пространственных подсказок. Backend — владелец расчётов.

Для голоса сначала можно озвучивать существующий text на телефоне без изменения API. Передача/распознавание аудио и input_mode=voice — отдельное расширение. Ошибка голоса не должна блокировать текст.

## 8. Ошибки, повторы и сохранность

```json
{
  "code": "inventory_mismatch",
  "message": "Not all observations have been accepted",
  "retryable": false,
  "details": {"missing_observation_ids":["OBSERVATION_UUID"],"extra_observation_ids":[],"hash_mismatches":[]}
}
```

| HTTP | Поведение клиента |
|---|---|
| 400 / 422 | Исправить запрос/данные; не повторять неизменённое автоматически |
| 401 / 403 | Остановить отправку, сохранить очередь; восстановить доступ |
| 404 | Проверить project/session/model/instruction, показать ошибку |
| 409 | Разобрать code/details; сверить состояние, не менять ID вслепую |
| 410 | Восстановить снимок instructions и курсор |
| 413 | Набор слишком велик; не менять RGB/depth без изменения метаданных и явного решения |
| 429 | Повтор после Retry-After |
| 5xx / сеть / timeout | Повтор той же операции с теми же ID и байтами, backoff с jitter |

Предлагаемый backoff: 1, 2, 4, 8… до 30 секунд; соблюдать Retry-After, если он больше. Конкретные таймауты/лимиты — согласовать с backend. Очередь сохранять до сети, восстанавливать после перезапуска, различать saved/accepted/processed. Локальные файлы не удалять после одного сетевого запроса; retention согласовать отдельно. Payloads кадров не писать в серверные/клиентские debug-логи.

## 9. Что нужно подтвердить коллеге

- [ ] Orchestrator API — единственная точка взаимодействия телефона; Geometry Service вызывается backend.
- [ ] Одно наблюдение = один RGB-D кадр; PLY формирует backend, либо отдельно согласовываем требование простого экспорта.
- [ ] Принимаем JPEG + float32 LE depth + uint8 confidence + JSON; nullable confidence.
- [ ] Принимаем направления матриц, единицы и сегменты; вместе проверяем реальный пример.
- [ ] Принимаем multipart, idempotency, durable acknowledgements и inventory completion.
- [ ] Принимаем polling, revisions и формат текстовых инструкций/ответов.
- [ ] Назначен владелец подготовки USDZ и таблицы element IDs.
- [ ] Предоставлены base URL, способ выдачи токена, лимиты и доступный тестовый сервер.
- [ ] Согласованы необходимость/формат alignment и coverage для следующего этапа.

## 10. Приёмка первой интеграции

1. Backend читает реальный JPEG/depth/confidence, проверяет хеши, размеры и intrinsics; восстанавливает точки без неявного изменения осей.
2. Перекрывающиеся реальные кадры проверены геометрически; зафиксирована измеренная ошибка, не обещание точности.
3. Прервать соединение во время upload и после приёма сервером: повтор не дублирует наблюдение; неполный набор не accepted.
4. Перезапуск приложения сохраняет ID/очередь; новый ARKit segment не смешивается со старым.
5. Текстовая инструкция приводит к дополнительному observation_id и ответу; дубликаты/старые revisions не портят UI.
6. Complete с отсутствующим/лишним кадром отклоняется; правильный inventory запускает обработку только один раз.
7. Mock/synthetic/recorded режимы видны явно; успешная загрузка не отображается как успешная инспекция.
