> **Superseded for the current first case:** use [Inspector server integration](inspector-server-integration.md) and [mobile implementation status](mobile-integration-status.md). The following document is an older architecture proposal.

# iPhone ↔ Geometry Service + AI Inspector API — v0.1, на согласование

Дата: 2026-10-27. **PROPOSED / НЕ СОГЛАСОВАНО.** Это предложение интерфейса, не описание уже работающего сервера. После согласования обе стороны реализуют один и тот же контракт.

## Дополнение: мобильная схема кадра 0.2 (27.10.2026)

По запросу Олега приложение теперь поддерживает телефоны без LiDAR. Это локальная реализация; приём новых кадров сервером **ещё требует согласования**. Ниже разделы v0.1 и их примеры остаются исходным предложением RGB-D; для новых кадров действует следующее изменение:

- `frame.json.schema_version` = `"0.2"` для новых RGB и RGB-D наблюдений. Старые кадры `0.1` читаются приложением. Версия manifest сессии остаётся `0.1`.
- Без LiDAR: `depth_source: "none"`; `depth`, `depth_size`, `depth_unit`, `depth_to_rgb_mapping`, `confidence` равны JSON `null`. В multipart такого наблюдения должны отсутствовать части depth/confidence. Нельзя подставлять нулевую или синтетическую глубину.
- JPEG, размеры RGB, `K_rgb`, `T_world_camera`, timestamp, session/segment/frame IDs сохраняются. Положение — оценка ARKit, а не привязка к BIM. Съёмка требует normal tracking.
- При наличии LiDAR формат глубины не меняется; кадр не сохраняется, пока sceneDepth не готова.
- Geometry Service должен явно различать RGB и RGB-D: отсутствие измеренной глубины не является измерением или подтверждением геометрического соответствия. Возможность обработки RGB-only и ответ на неподдерживаемый тип требуется согласовать.
- Описания depth/confidence encoding в manifest применимы только к кадрам, где соответствующий файл присутствует.

## 1. Кто что реализует

```text
                     iPhone app
                      ↙     ↘
             HTTPS /         \ HTTPS /
             RGB-D, модели    задания, ответы
                  ↕               ↕
          Geometry Service ↔ AI Inspector
                  прямое межсервисное API
```

**Подтверждённая схема:** телефон напрямую общается с обоими сервисами; сервисы также общаются друг с другом. AI Inspector не является обязательным прокси для загрузки RGB-D или доступа к Geometry Service. Ниже конкретные маршруты и порядок синхронизации предложены для согласования.

- **Geometry Service (G)**: приём и хранение RGB-D, проверка файлов, геометрическая обработка, модель/привязка/покрытие. Предлагаем также выдавать через него подготовленный BIM-пакет; владелец конвертера ещё согласуется.
- **AI Inspector (I)**: задания, текстовые ответы, AI-анализ и итог инспекции. Если внутри него используется оркестратор, это деталь реализации, не единая точка входа телефона.
- **Приложение iPhone**: два HTTP-клиента/адаптера, локальное сохранение, очередь загрузок G и отдельная очередь ответов I. Входящий HTTP-сервер на телефоне не нужен.
- **G ↔ I**: Inspector получает метаданные, файлы и геометрию напрямую у G; G уведомляет Inspector о принятии и готовности данных. Телефон не пересылает результаты между микросервисами.
- Ключи моделей остаются на backend. Телефон не вызывает поставщиков AI-моделей напрямую.
- **Одно наблюдение = одно фото с соответствующими depth и метаданными**, не батч.
- Наличие PLY во входе Geometry Service не делает его обязательным выходом телефона: предлагаем преобразование RGB-D в PLY на стороне G либо его backend-адаптера. Это всё ещё отдельный пункт согласования с владельцем Geometry Service.

## 2. Реализованное и предлагаемое

Уже в iOS-коде: локальная сессия и файлы наблюдения, UUID, SHA-256, синтетический источник, код захвата ARKit. Формат записывается в [`Observation.swift`](../ios/AIInspector/Observation.swift).

**Не реализовано:** сетевой клиент, загрузка на сервер, авторизация, получение событий, BIM import, backend alignment/coverage, голос. Локальная демоинструкция не является ответом AI. Получение реальных RGB-D и их геометрическая корректность ещё не проверены на iPhone.

Документ ниже сохраняет существующий формат `frame.json`. Новые сетевые оболочки (project_id, app_version, ревизии, квитанции, состояние загрузки) требуют реализации. Существующий `SessionManifest` не нужно автоматически считать полным HTTP-запросом.

## 3. Общие правила

- Два конфигурируемых адреса: `geometry_base_url` (G) и `inspector_base_url` (I). Даже одинаковый путь на них — разные API. Для удалённых сервисов — HTTPS. Реальные адреса предоставляют владельцы сервисов.
- `Authorization: Bearer <token>`. Способ выдачи/обновления согласовать; права ограничены проектом и сервисом (audience). Конфигурация клиента допускает разные токены G/I; хранить их в Keychain, не пересылать автоматически между хостами. Межсервисные вызовы используют отдельную service identity, недоступную приложению.
- JSON UTF-8; даты UTC RFC 3339. `timestamp_s` кадра — монотонное время ARKit, **не UTC**.
- UUID генерирует клиент для session, segment, observation, response и completion. Передавать один и тот же строковый UUID во всех ссылках и повторах. ID проекта/комнаты/модели выдаёт backend; ID инструкций — I, ID событий — сервис-источник. Курсоры и revisions имеют область действия сервиса/ресурса, а не глобальную нумерацию.
- `schema_version: "0.1"`. Неизвестную версию отклонять с 422. Добавление необязательных полей допустимо; изменение единиц/осей/значения существующих полей требует новой версии.
- Ошибки и квитанции — JSON. HTTP 2xx не означает успешную инспекцию.
- Для MVP один телефон пишет в одну сессию. Несколько телефонов — отдельные сессии.
- Лимиты размера наблюдения, числа наблюдений и частоты запросов согласовать перед интеграцией. Не обрезать изображения или depth молча.

## 4. Сценарий MVP

1. Телефон получает подготовленную модель у **G**, создаёт один session_id и регистрирует неизменяемую конфигурацию сессии сначала у G, затем у **I** с тем же ID.
2. У G регистрируется segment_id. Захват можно сохранять локально без сети; отправка в G возможна после регистрации там. AI-задания начинаются после успешной регистрации I.
3. Телефон сохраняет наблюдение и напрямую загружает RGB-D в **G**. Только durable receipt G увеличивает счётчик «Отправлено».
4. **G напрямую уведомляет I** о принятии и затем готовности данных. I сам получает у G необходимые изображения/метаданные/геометрию.
5. Телефон опрашивает **I** для инструкций, **G** — для состояния геометрии. Эти два статуса независимы.
6. На запрос досъёмки телефон загружает дополнительный кадр в G с instruction_id, затем отправляет в I ответ с observation_id. I проверяет факт приёма у G; доставка уведомления может запаздывать.
7. **I** определяет достаточность evidence и управляет заданиями; кнопка/загрузка сами по себе не закрывают задание.
8. Телефон отправляет запрос завершения в **I**. I замораживает выдачу заданий и напрямую согласует закрытие набора с G. Это одна координирующая операция, а не проксирование загрузок через Inspector.

## 5. Маршруты телефона

| Сервис | Метод и путь | Назначение | Успех |
|---|---|---|---|
| G | `GET /v1/projects/{project_id}/models/{model_version}` | Подготовленная модель и BIM ID | 200 |
| G | `PUT /v1/sessions/{s}` | Зарегистрировать сессию данных | 201 / 200 повтор |
| I | `PUT /v1/sessions/{s}` | Зарегистрировать инспекцию той же сессии | 201 / 200 повтор |
| G | `PUT /v1/sessions/{s}/segments/{g}` | Сегмент координат | 201 / 200 повтор |
| G | `PUT /v1/sessions/{s}/observations/{o}` | Одно наблюдение, multipart | 201 / 200 повтор |
| G | `GET /v1/sessions/{s}/uploads?after={cursor}&limit=100` | Сверка принятых наблюдений | 200 |
| G | `GET /v1/sessions/{s}` | Статус геометрии и ссылки на результаты | 200 |
| I | `GET /v1/sessions/{s}/events?after={cursor}&limit=100` | Задания и обновления инспекции | 200 |
| I | `GET /v1/sessions/{s}/instructions` | Снимок заданий и курсор I | 200 |
| I | `POST /v1/sessions/{s}/instructions/{i}/responses` | Ответ пользователя | 201 / 200 повтор |
| I | `POST /v1/sessions/{s}/complete` | Согласованное завершение съёмки | 202 / 200 повтор |
| I | `GET /v1/sessions/{s}` | Статус AI-инспекции и итог | 200 |

`s` = session_id, `g` = segment_id, `o` = observation_id, `i` = instruction_id. Курсоры непрозрачные строки. Примеры схематические: `SESSION_UUID` и подобные значения заменяются реальными ID; нулевые хеши — placeholders, не тестовые файлы для приёма сервером.

### 5.1 Модель для отображения

Ответ **G** GET model:

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

URL ассета — относительный путь на API Geometry Service или подписанный HTTPS URL. Bearer token приложения не пересылать стороннему хосту. Backend определяет срок действия подписанного URL; при истечении клиент повторяет GET model. `model_version` неизменяема; новые геометрия/ID требуют новой версии.

### 5.2 Сессия

Одинаковый JSON в **G** PUT session и **I** PUT session:

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

Квитанции регистрации: G — `{"session_id":"SESSION_UUID","capture_state":"open","revision":1}`; I — `{"session_id":"SESSION_UUID","state":"capturing","revision":1}`. Это сокращённые receipts, полные состояния см. §5.7.

**G — источник истины для конфигурации сессии и evidence; I — для заданий и итогов инспекции.** I при регистрации читает конфигурацию у G через межсервисный GET (§5.8) и проверяет совпадение всех полей. Неизвестная у G сессия → 409 `geometry_session_missing`, разные параметры → 409 `session_config_mismatch`, G недоступен → 503. I не создаёт отдельный session_id.

ID пути и тела совпадают. Повтор с теми же полями — 200; другое содержимое того же ID — 409 `idempotency_conflict`. PUT не используется для произвольного обновления. Клиент сохраняет исходное тело и два независимых признака регистрации. Если G принял регистрацию, а I недоступен, данные в G не откатываются: клиент повторяет регистрацию I с тем же ID; UI показывает «Инспектор не подключён». G не требует успешной регистрации I для приёма RGB-D.

Поле parent_session_id связывает досъёмку с предыдущей сессией того же проекта и модели. Оба сервиса проверяют доступ к родителю. Авторизованные сервисы получают конфигурацию по фиксированным backend-адресам, не по произвольному URL из клиентского запроса.

### 5.3 Сегменты координат

**G** PUT segment, JSON:

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

**G** PUT observation: `Content-Type: multipart/form-data; boundary=...`.

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

Размеры, intrinsics и поза в примере условные, не результаты измерения. Geometry Service проверяет JPEG dimensions, бинарные размеры, хеши, форму/конечность матриц, положительные focal lengths, валидность rigid transform и принадлежность segment/session. Для v0.1 `frame_id == observation_id`. `instruction_id` — контекст съёмки, а не доказательство принадлежности пикселей конкретному BIM-объекту. G сохраняет instruction_id как ссылку, не блокируя upload на доступности I и не определяя актуальность задания. Поздний кадр со старой инструкцией остаётся evidence; принадлежность инструкции сессии и переход её состояния проверяет I при обработке ответа.

Квитанция **G** возвращается **после долговременного сохранения и проверки всего набора**:

```json
{
  "session_id": "SESSION_UUID",
  "observation_id": "OBSERVATION_UUID",
  "status": "accepted",
  "metadata_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "accepted_at": "2026-10-27T10:00:06Z"
}
```

`metadata_sha256` считается по точным байтам multipart-части `metadata`. Клиент повторно отправляет сохранённый frame.json, не генерирует новые даты/форматирование. Multipart boundary может меняться. Одинаковые ID и metadata/file hashes → 200; изменённый набор с тем же ID → 409. Частично полученный набор не виден в uploads и не порождает межсервисное observation.accepted. Приём кадра G не означает завершение геометрической обработки или инспекции.

### 5.5 Сверка загрузок

**G** GET uploads: `{"items":[{"observation_id":"OBSERVATION_UUID","metadata_sha256":"HASH"}],"next_cursor":null}`.

Страницы отсортированы по серверному порядку приёма. `next_cursor: null` означает конец текущего списка. Клиент сверяет ID **и** metadata hash; совпадение одного ID недостаточно. Сетевой timeout после PUT не доказывает потерю записи: повторить тот же PUT или свериться с uploads. Изменять ID для повтора нельзя.

### 5.6 События и инструкции

События и инструкции обслуживает **I**. Для MVP — foreground polling; предлагаем интервал 2 секунды при активном экране. При ошибках backoff, без параллельных polling-запросов. Фоновый push/WebSocket не требуется в первой интеграции.

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
- Типы MVP: `instruction.created`, `instruction.updated`, `instruction.cancelled` — каждый содержит полный объект instruction; `session.updated` содержит объект состояния из **I** GET session. Неизвестный тип можно пропустить с логированием, не ломая polling.
- `revision` инструкции монотонно растёт. Клиент не применяет старую/равную ревизию поверх новой. Состояния: `pending`, `collecting`, `satisfied`, `cannot_complete`, `cancelled`.
- При истечении курсора — 410 `cursor_expired`. GET instructions возвращает `{"instructions":[...],"resume_cursor":"..."}`: полный текущий снимок заданий, включая закрытые, и курсор одной атомарной серверной точки. Клиент заменяет локальный список, запрашивает **I** GET session, продолжает events с resume_cursor.
- Для v0.1 `target_region` и `audio` всегда null. Их непустые схемы согласовать отдельно; текст остаётся основным представлением. Не получать пространственные координаты разбором текста.

**I** POST response, JSON:

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

I проверяет принятые наблюдения непосредственно в G, если их ещё нет в локальной проекции событий. Не полагается только на утверждение телефона. Неизвестный ID → 409 `evidence_not_accepted`, G недоступен → 503; клиент сохраняет ответ в отдельной очереди и повторяет без повторной загрузки файлов. Геометрия ещё обрабатывается — допустимо принять ответ и оставить задание collecting.

Действия: `acknowledged`, `observations_submitted`, `cannot_complete`, `message`. Для observations_submitted нужен непустой список **принятых** наблюдений этой сессии, связанных с instruction_id; для остальных — пустой список. Для message нужен непустой text; для cannot_complete text может содержать причину. MVP input_mode: `button | typed`; `voice` — будущее расширение.

Ответ: `{"response_id":"RESPONSE_UUID","status":"accepted"}`. Это приём ответа, не подтверждение достаточности evidence. При изменившейся instruction revision — 409 `stale_instruction` с актуальной инструкцией в details. При новой осознанной отправке после конфликта — новый response_id. Успешный повтор того же response_id/body возвращает исходную квитанцию **до** проверки текущей revision; другой body того же ID — 409.

### 5.7 Завершение и два независимых состояния

**I** POST complete, JSON:

```json
{
  "schema_version": "0.1",
  "completion_id": "COMPLETION_UUID",
  "observations": [
    {"observation_id":"OBSERVATION_UUID","metadata_sha256":"0000000000000000000000000000000000000000000000000000000000000000"}
  ]
}
```

Перед запросом клиент останавливает захват, фиксирует непустой inventory без дубликатов и ждёт всех квитанций G. Активные pending/collecting задания у I → 409 `instructions_pending`. Повтор успешно принятого completion_id/body возвращает квитанцию той же операции; изменённый body того же ID → 409. Другой completion_id при незавершённой операции закрытия → 409 `completion_in_progress`.

**Предложенный протокол закрытия между сервисами:**

1. I атомарно сохраняет completion-запрос, ставит state=closing, completion.status=pending и прекращает создавать/менять задания съёмки. Отвечает 202 после сохранения операции: `{"session_id":"SESSION_UUID","completion":{"completion_id":"COMPLETION_UUID","status":"pending","error":null}}`. Повторы — 200 с текущим состоянием той же операции, не новые задания обработки.
2. I вызывает служебный seal у G (§5.8) с тем же completion_id и inventory. G атомарно сверяет точное множество ID/hash и закрывает приём новых ID. Это исключает гонку между сверкой и upload.
3. При inventory mismatch G отвечает 409, оставляя набор открытым. I отмечает completion.status=rejected с деталями, возвращается в capturing и публикует session.updated. Клиент исправляет inventory/дозагружает данные и создаёт **новый** completion_id. Повтор старого запроса возвращает ту же отклонённую операцию, не запускает её заново.
4. При timeout/недоступности G I остаётся closing и повторяет seal с тем же ID. Нельзя считать набор открытым или возвращаться в capturing, пока результат неизвестен: G мог уже успешно закрыть его. Состояние операции хранится долговременно, переживает рестарт I.
5. После подтверждения seal I ставит completion.status=sealed, state=waiting_geometry. G строит окончательную геометрию по зафиксированному набору. Затем I переходит в analyzing и completed (либо failed), привязав анализ к конкретной geometry_revision.

У G после seal новые наблюдения/сегменты → 409 `session_closed`; точный повтор уже принятого наблюдения допустим. При ошибке геометрии закрытый набор не открывается автоматически. Детали повторного запуска backend-обработки согласовать отдельно; новые данные после закрытия отправляются в follow-up session.

**G GET session** — геометрия:

```json
{
  "session_id": "SESSION_UUID",
  "revision": 8,
  "capture_state": "open",
  "processing_state": "processing",
  "accepted_observation_count": 12,
  "completion_id": null,
  "geometry_revision": 3,
  "evidence_observation_ids": ["OBSERVATION_UUID"],
  "artifacts": [],
  "alignment": null,
  "coverage": null,
  "error": null
}
```

`capture_state`: open | sealed; `processing_state`: idle | processing | ready | failed. `revision` — версия состояния G; `geometry_revision` — nullable номер последнего **готового** неизменяемого результата. Пока строится следующий результат, предыдущий может быть доступен; его evidence_observation_ids явно указывают использованные кадры. При geometry_revision=null список evidence/artifacts пуст. Готовность геометрии по подмножеству не означает готовность всех 12 принятых кадров. Финальная геометрия после seal должна явно покрывать весь inventory либо иметь error; не скрывать исключённые/необработанные кадры.

`artifacts`: список `{artifact_id, kind, url, media_type, byte_length, sha256}`, где kind = point_cloud | reconstructed_model; тип файла задаёт media_type (формат согласовать, например PLY для point_cloud). Все элементы относятся к указанной geometry_revision. URL — путь G либо подписанный HTTPS URL. Отсутствие артефактов явно представляется пустым списком. Alignment и coverage пока null (§7).

**I GET session / session.updated payload** — инспекция:

```json
{
  "session_id": "SESSION_UUID",
  "revision": 5,
  "state": "closing",
  "completion": {"completion_id":"COMPLETION_UUID","status":"pending","error":null},
  "geometry_revision": null,
  "result": null,
  "error": null
}
```

`state`: capturing | closing | waiting_geometry | analyzing | completed | failed. `completion` = null до первой операции; затем последняя операция со status pending | rejected | sealed. Отклонение закрытия относится к completion.error, сбой анализа — к верхнеуровневому error. Операции дедуплицируются и сохраняют собственное состояние; повтор старого completion_id возвращает состояние именно этой операции: `{"session_id":"...","completion":{...}}`, а актуальное состояние всегда берётся через GET session. `geometry_revision` I — версия геометрии, используемая текущим/завершённым анализом; не обязана равняться самой новой версии G. Клиент хранит revisions обоих сервисов отдельно.

До closing I может анализировать evidence и выдавать задания, оставаясь в capturing. После закрытия новые задания съёмки не создаются в этой сессии. Если финальный анализ требует досъёмки, result.outcome=needs_more_data; пользователь создаёт новую сессию у G и I с parent_session_id, и I создаёт задания уже для неё.

Для I completed поле result:

```json
{
  "outcome": "needs_more_data",
  "analysis_origin": "mock",
  "summary": "Недостаточно данных о правой стороне проёма.",
  "evidence_observation_ids": ["OBSERVATION_UUID"],
  "report_url": null
}
```

`outcome`: issues_found | no_issues_detected | needs_more_data; `analysis_origin`: live_model | recorded_model | mock. Evidence IDs принадлежат этой сессии. `no_issues_detected` — результат текущего анализа, не сертифицированная приёмка. report_url — nullable HTTPS URL существующего отчёта. Ошибки используют формат §8. Загрузка, готовность геометрии и итог инспекции — три отдельных состояния UI.

### 5.8 Прямое взаимодействие микросервисов (предложение MVP)

Эти маршруты вызывают **сервисы**, не телефон. Для MVP предлагаем HTTP + долговременную очередь уведомлений; брокер событий не обязателен. Конкретный транспорт согласуется владельцами backend. Межсервисная авторизация проверяет identity отправителя и доступ к проекту.

| Принимающий сервис | Служебный маршрут | Кто вызывает / зачем |
|---|---|---|
| G | `GET /internal/v1/sessions/{s}/registration` | I: исходная конфигурация из PUT session, без изменений |
| G | `GET /internal/v1/sessions/{s}/observations/{o}` | I: точный frame.json и доступ к исходным файлам |
| G | `GET /internal/v1/sessions/{s}/observations/{o}/files/{name}` | I: rgb.jpg, depth.f32, confidence.u8 или frame.json; другие имена запрещены |
| G | `POST /internal/v1/sessions/{s}/seal` | I: completion_id + inventory из POST complete; закрыть набор атомарно |
| I | `POST /internal/v1/geometry-events` | G: уведомить о приёме/геометрии/ошибках |

I также вызывает G GET uploads и GET session из §5 со служебной авторизацией для сверки и получения результатов. Регистрационные данные возвращаются в точности в формате §5.2. GET observation возвращает `{"metadata":{...frame.json...},"metadata_sha256":"...","files":[{"name":"rgb.jpg","url":"/internal/v1/.../files/rgb.jpg"}]}`; список files включает все сохранённые файлы, включая frame.json. Для проверки metadata_sha256 использовать **исходные байты frame.json** из file endpoint, не пересериализованный metadata. G проверяет scope на каждом запросе, данные не становятся публичными по знанию UUID. Файлы геометрии I получает по artifact URLs из G GET session.

Seal: 200 `{"session_id":"SESSION_UUID","completion_id":"COMPLETION_UUID","capture_state":"sealed"}` только после durable commit. Точный повтор возвращает тот же результат даже при последующих сбоях обработки; inventory mismatch → 409 `inventory_mismatch`, набор остаётся open. Если уже закрыт другим completion_id → 409 `session_closed`; I должен сверить состояние и показать конфликт, а не бесконечно повторять. Для успешного seal G гарантирует постановку финальной обработки в восстанавливаемую очередь.

Пример G → I:

```json
{
  "schema_version": "0.1",
  "event_id": "geometry-evt-17",
  "type": "observation.accepted",
  "project_id": "demo-building",
  "session_id": "SESSION_UUID",
  "model_version": "demo-room-v1",
  "source_revision": 8,
  "created_at": "2026-10-27T10:00:06Z",
  "observation_id": "OBSERVATION_UUID",
  "metadata_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "geometry_revision": null,
  "completion_id": null,
  "error": null
}
```

Типы: observation.accepted (обязательны observation_id/hash), geometry.ready (geometry_revision обязателен; observation_id/hash=null), geometry.failed (error обязателен; observation_id/hash=null). geometry_revision при failure = null; событие не обозначает готовый результат. Для финальной обработки после seal completion_id обязателен; для промежуточной — null. source_revision — версия состояния G на момент события, **не** revision I. Список evidence и ссылки на артефакты I читает в G; историческая geometry_revision из события может уже быть заменена в текущем состоянии — I использует явно выбранную актуальную версию, а не выдаёт её за старую.

I отвечает 202 `{"event_id":"geometry-evt-17","status":"queued"}` после durable записи в inbox; точный повтор — 200, другое содержимое того же event_id — 409. G фиксирует обязательство отправить событие вместе с принятием кадра/результата (outbox либо эквивалент), повторяет до подтверждения. Если сессия ещё не зарегистрирована в I, I сохраняет уведомление до регистрации и не запускает анализ без проверки конфигурации/прав.

События могут дублироваться и приходить не по порядку. Не отбрасывать observation.accepted только потому, что уже видели большую source_revision: это другой кадр. I дедуплицирует event_id и observation_id/hash, а состояние геометрии сверяет с G. После своей регистрации/перезапуска I сверяет GET uploads и GET session, восстанавливая пропущенные уведомления. Телефон в этом восстановлении не участвует и файлы между сервисами не переносит.

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

Для будущего alignment нужны минимум model_version, segment_id, alignment_id, revision, status и T_bim_world. Для покрытия — model_version, revision, ссылки на evidence и явное отличие unknown от измеренного. До согласования/получения валидных данных телефон показывает модель отдельно, без позиции камеры, процента покрытия и пространственных подсказок. Geometry Service — владелец расчётов; телефон получает их непосредственно у G. AI Inspector получает те же результаты межсервисным запросом.

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

- [ ] Прямые API телефона к G и I; G ↔ I без участия телефона. Утверждены владельцы маршрутов и межсервисная авторизация.
- [ ] Одно наблюдение = один RGB-D кадр; PLY формирует backend, либо отдельно согласовываем требование простого экспорта.
- [ ] Принимаем JPEG + float32 LE depth + uint8 confidence + JSON; nullable confidence.
- [ ] Принимаем направления матриц, единицы и сегменты; вместе проверяем реальный пример.
- [ ] Принимаем multipart, idempotency, durable acknowledgements G; регистрацию общей сессии и координированное закрытие через I → G seal.
- [ ] Принимаем polling, revisions и формат текстовых инструкций/ответов.
- [ ] Назначен владелец подготовки USDZ и таблицы element IDs.
- [ ] Предоставлены два base URL, способы выдачи клиентских токенов, лимиты и тестовые серверы.
- [ ] Утверждён механизм G → I уведомлений, inbox/outbox и восстановление состояния без телефона.
- [ ] Согласованы необходимость/формат alignment и coverage для следующего этапа.

## 10. Приёмка первой интеграции

1. Backend читает реальный JPEG/depth/confidence, проверяет хеши, размеры и intrinsics; восстанавливает точки без неявного изменения осей.
2. Перекрывающиеся реальные кадры проверены геометрически; зафиксирована измеренная ошибка, не обещание точности.
3. Прервать соединение во время upload и после приёма сервером: повтор не дублирует наблюдение; неполный набор не accepted.
4. Перезапуск приложения сохраняет ID/очередь; новый ARKit segment не смешивается со старым.
5. Текстовая инструкция приводит к дополнительному observation_id и ответу; дубликаты/старые revisions не портят UI.
6. Неверный inventory приводит к rejected completion без закрытия G; правильный seal закрывает набор один раз. Timeout после seal и рестарт I не открывают его повторно.
7. При недоступном I телефон всё ещё загружает в G; после восстановления I получает данные напрямую и выдаёт задания. События до регистрации I, дубликаты и обратный порядок доставки не теряют evidence.
8. Mock/synthetic/recorded режимы видны явно; успешная загрузка не отображается как успешная инспекция.

## 11. Изменение схемы, 2026-10-27

Эта редакция заменяет первоначальную схему единого входа через Orchestrator API. Имя файла сохранено, чтобы ранее отправленная ссылка продолжала работать. Формат frame.json и schema_version 0.1 не меняются; транспорт ещё не был реализован/согласован. Подтверждена топология с двумя прямыми подключениями телефона; детали регистрации, внутренних уведомлений и seal — предложение для коллег, а не утверждённая реализация.
