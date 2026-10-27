# Первая версия: iPhone → ссылка на съёмку → Geometry Service

В исходном контракте генератор называется **Geometry Service (G)** или Geometry
Builder. Backend Inspector создаёт сессию, получает от iPhone ссылку на завершённый
набор и передаёт принятый источник генератору. Языковая модель в этом процессе не
вызывается.

## Последовательность

1. Создать инспекцию и capture session существующими endpoint.
2. iPhone получает назначенную сессию через `GET capture-assignments`, сохраняет ID
   и при желании подтверждает получение через `POST acknowledgements`.
3. При начале съёмки приложение отправляет `POST capture-start`.
4. Приложение загружает кадры в S3, последним — `capture_manifest_v1` со ссылками
   на конкретные версии файлов.
5. Приложение отправляет `POST /v1/sessions/{session_id}/complete`.
6. Backend сохраняет сообщение и отвечает `202`. Фоновый worker проверяет manifest,
   принадлежность данных сессии и целостность перечисленных версий файлов.
7. После успешной проверки backend фиксирует источник, закрывает capture для новых
   данных и создаёт задание генератору. Worker передаёт задание, когда настроен адрес
   генератора; пока адрес пуст, оно остаётся в сохраняемой очереди.

## Сообщение от iPhone

```json
{
  "schema_version": "0.1",
  "completion_id": "e6d4920b-352a-4c2f-8f1e-4de4b0bfdbdf",
  "capture_ref": {
    "storage_id": "capture-s3",
    "manifest_key": "projects/demo-building/sessions/SESSION_UUID/manifests/e6d4920b-352a-4c2f-8f1e-4de4b0bfdbdf.json",
    "manifest_version_id": "EXACT_S3_VERSION_ID",
    "manifest_byte_length": 4096,
    "manifest_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

`SESSION_UUID`, размер, hash и version ID в примере — placeholders. Приложение
передаёт реальные значения. Backend выбирает bucket из конфигурации по storage ID;
клиент не задаёт произвольный bucket или внешний URL. Ключ manifest должен совпадать
с выделенным session prefix и completion ID. Требуется токен назначенного устройства.

Первый ответ:

```json
{
  "session_id": "SESSION_UUID",
  "completion": {
    "completion_id": "e6d4920b-352a-4c2f-8f1e-4de4b0bfdbdf",
    "status": "pending",
    "error": null
  }
}
```

Точный повтор ID/содержимого возвращает `200` с **текущим статусом этой операции**.
Другое содержимое под тем же ID — `409`. При pending другая completion запрещена.
После постоянной ошибки файлов нужна новая completion и новый manifest; повтор
старой операции возвращает сохранённый отказ, даже если новый набор уже принят.

Полученный `capture_ref` и статус видны оператору в `GET /v1/sessions/{session_id}`.
Это позволяет получить ссылку от телефона без отдельного webhook на пользовательский
компьютер.

## Проверка и состояния

- `submitted / validating`: сообщение сохранено, проверка ещё не завершена.
- Если bucket не настроен либо S3 временно недоступен, completion остаётся pending;
  причина доступна в `source.validation_error`.
- Отсутствующий файл, неверный hash или некорректный manifest дают rejected;
  capture возвращается в `capturing`, задание генератора не создаётся.
- После проверки: `sealed / queued_geometry`, completion accepted, источник
  закреплён по точным версиям и hashes.
- Пустой адрес генератора: `geometry_job.dispatch_status=pending` с причиной
  `geometry_not_configured` после первой попытки доставки.
- Получено подтверждение генератора: `dispatch_status=delivered`. Это означает
  приём задания генератором, а не готовность геометрии или результат инспекции.

Проверяются manifest 0.1, frame.json 0.1/0.2, обязательные RGB/metadata, условные
depth/confidence, IDs/segments, размеры и SHA-256. Для доверенного полного checksum
S3 не требуется скачивать каждый большой файл; при отсутствии подходящего checksum
байты читаются потоком и hash вычисляется заново. Геометрическое качество остаётся
за Geometry Service. До появления истории инструкций поддерживается
`frame.instruction_id=null`. RGB-only наборы можно принять как визуальные данные;
передача таких наборов генератору требует явного включения его поддержки.

## Передача в генератор

Backend вызывает предложенный контрактом endpoint генератора:

```text
PUT {INSPECTOR_GEOMETRY_BASE_URL}/internal/v1/jobs/{job_id}
```

В запросе находятся job/operation/session/inspection/project/room/model/profile IDs
и `source_ref`:

```json
{
  "source_ref": {
    "url": "https://inspector.example/internal/v1/sessions/SESSION_UUID/capture-source",
    "manifest_sha256": "EXACT_MANIFEST_SHA256",
    "manifest_version_id": "EXACT_MANIFEST_VERSION_ID"
  }
}
```

Это служебная ссылка Inspector. Генератор с отдельным токеном читает:

- `GET /internal/v1/sessions/{session_id}/capture-source` — временный URL точной
  версии manifest, hash/размер и адрес списка файлов.
- `GET /internal/v1/sessions/{session_id}/capture-source/files?after=...&limit=100`
  — страницы временных URL только принятых версий файлов.

Генератор скачивает файлы напрямую из S3. Телефон не вызывает генератор. Временные
подписи выдаются заново по запросу и не записываются в состояние задания.

Отправка автоматическая. Для ручного ускорения pending retry оператор может вызвать
`POST /v1/sessions/{session_id}/geometry-jobs/dispatch`: `202`, если задание ожидает
доставки; `200`, если оно уже доставлено; `409`, если источник ещё не принят либо
генератор окончательно отклонил это задание. Endpoint не создаёт новую обработку.

При timeout, 429 и временных ошибках сохраняются job ID, operation key и точные
байты исходящего запроса. `Retry-After` учитывается. Redirect не выполняется.
Результаты ready/failed теперь сохраняются: callback принимается в durable inbox,
затем backend читает точное состояние задания через GET. Опрос того же job также
выполняется без callback. Подтверждение ready в квитанции отправки само по себе
не применяется как проверенная геометрия. Терминальный результат доступен в
`session.geometry_result`; `processing_scope=geometry_only_v1` явно ограничивает
смысл `processing_state=completed` первым кейсом, без результата BIM-инспекции.

## Настройки и границы

Адрес генератора оставлен пустым по запросу пользователя. Для подключения потребуются
`INSPECTOR_CAPTURE_BUCKET`, AWS-доступ к версиям объектов,
`INSPECTOR_GEOMETRY_BASE_URL`, доступный генератору `INSPECTOR_PUBLIC_BASE_URL` и два
разных service-токена. Полный список находится в `.env.example`.

SQLite автоматически мигрирует с версии схемы 1 на 2, сохраняя имеющиеся инспекции,
сессии и квитанции. В версии 2 добавлены completions, geometry jobs и сохраняемая
очередь с leases и восстановлением после сбоя worker.

Версия схемы 3 дополнительно сохраняет готовность папки Supabase. При включённой
Supabase-конфигурации новая сессия получает папку с её ID; назначение становится
видимым телефону после подтверждения создания marker-файла. Существующие сессии
при миграции остаются доступными. Настройки читаются из локального `.env`; переменные
процесса имеют приоритет.

Версия схемы 4 добавляет историю событий устройства, соединения, upload requests /
objects и inbox Geometry callbacks. WebSocket устройства работает до создания
сессии, поддерживает snapshot/replay и доставляет session ID после готовности папки.

Supabase использует отдельный реализованный формат `capture_manifest_supabase_v1`
и completion `format=supabase_capture_v1`, без фиктивных S3 version IDs. Backend
выдаёт signed upload plans для точных raw-ключей, проверяет набор и сохраняет
server-owned копии принятых байтов. Ссылки G ведут к этим копиям, сохраняя исходный
manifest/hash и логические object keys. AWS S3-сессии используют versioned контракт
выше. Подробная актуальная инструкция: `ios-integration.md`.

Удалённый тест проходит через настоящий Supabase и HTTPS/WSS tunnel. Опциональный
тестовый Geometry Service возвращает `analysis_origin=mock`; реальная реконструкция
требует адреса настоящего G. iPhone-разработчику передаются scoped device-токен и
сетевой контракт, серверный Supabase secret остаётся только на backend.
