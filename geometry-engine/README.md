# Geometry Engine

## HTTP: download and process

Start with `python main.py`; Swagger: `http://127.0.0.1:8000/docs`.

- `POST /sessions/extract`: `{"session_id":"FULL_SESSION_ID","project_id":"final_test"}`.
  Download and unpack into `data/raw/ID/original`; no geometry processing.
- `POST /sessions/process`: `{"session_id":"FULL_SESSION_ID"}`.
  Reconstruction -> normalized input -> validation -> mesh preprocessing -> frame selection
  -> Meta SAM -> mask projection and mesh segmentation -> wall planes and plane merging.

The pipeline accepts raw capture v1, flat or multipart. Install
`requirements-reconstruction.txt` and configure `MODEL_API_KEY` first.
All selected frames are sent to Meta SAM for wall/window/door detection.
This is a synchronous request: the response arrives after all stages finish.
Configure proxy timeouts accordingly; processing may continue after client disconnect.

Outputs remain in `data/processed/ID/`. `pipeline.json` records status, current stage
and completed stages. Failures preserve completed outputs. Repeated/concurrent runs
return 409; automatic full-pipeline resume is not implemented. Individual stage CLIs remain available.
Input errors return 422, Meta failures 502, and a missing Meta key 503.
The `input` folder combines metadata and the reconstructed mesh. Large assets use hard links
when available, otherwise copies. Both raw and derived inputs must remain read-only.
The output is a segmented mesh and candidate wall planes, not final opening measurements.


## Raw and processed storage

`GEOMETRY_DATA_DIR` is the common data root, not its raw/processed subfolder.
Downloads publish `data/raw/ID/original/` and keep ZIPs in `data/raw/ID/archives/`.
All stages write into `data/processed/ID/`. Pass either `data/raw/ID` or
`data/processed/ID` to `--session`; output still goes into processed.

Existing manifest references use a logical session namespace: `original/...`
resolves to the raw session, and stage paths resolve to the processed session.
This preserves portable references and hashes without copying source photographs
or creating symlinks. Legacy session directories remain readable.
A derived input snapshot may live in `processed/ID/input/`; reconstruction always
reads the original raw capture, even when this snapshot exists.


Основной код восстанавливает mesh из сырых RGB-D кадров и обрабатывает треугольную сетку в метрах. Он доступен как Python-пакет `geometry_engine` для подключения к сервису. Подготовка данных Apple ARKitScenes и диагностические картинки отделены в `tools/arkitscenes/`. Реконструкция запускается через `python run_reconstruction.py --session data/processed/ID`; подробности ниже в разделе «Реконструкция из сырых кадров».

## Структура

```text
geometry-engine/
├── geometry_engine/          # Основная логика
│   ├── preprocessing.py      # Обработка mesh и сохранение результата
│   ├── frame_filter.py       # Отбор качественных разных ракурсов для SAM
│   ├── segmentation.py       # Маски, фильтрация и сохранение результатов SAM
│   ├── sam_backend.py        # Вызов SAM 3.1 через Meta Model API
│   ├── mask_projection.py    # Перенос масок на видимые треугольники с проверкой depth
│   ├── visibility.py         # CPU z-buffer: ближайшая поверхность в каждом пикселе
│   ├── mask_fusion.py        # Согласование классов и наблюдений между ракурсами
│   ├── mesh_segmentation.py  # Запуск переноса, объединения и сохранение результата
│   ├── segmented_mesh_export.py # PLY с метками и автономный 3D-просмотр
│   ├── wall_plane_detection.py # RANSAC: плоскости и поддерживающие треугольники
│   ├── wall_planes.py        # Этап поиска плоскостей в сессии
│   ├── wall_plane_viewer.py  # Полупрозрачные плоскости поверх mesh
│   ├── mesh.py               # Геометрические операции, нормали и BVH
│   ├── cli.py                # Разбор аргументов командной строки
│   └── __main__.py           # Запуск python -m geometry_engine
├── tools/arkitscenes/        # Локальные инструменты, исключены из Git
│   ├── download_scene.py     # Скачивание сцены Apple
│   ├── structure_scene.py    # Преобразование файлов в наш формат frames
│   └── check_mesh.py         # Проверки проекции и картинки на данных Apple
├── tests/                    # Проверки основного кода
├── pyproject.toml
└── data/                     # Локальные данные, исключены из Git
```

## Основная точка входа

Для последовательного запуска проверки сессии, preprocessing mesh и отбора кадров:

```powershell
python run_geometry.py
```

По умолчанию используется сессия `data/processed/47333462`. Поддерживаются входные форматы `capture.json` (контракт телефона v1) и `scene.json` с `frames.jsonl` (structured). Исходные файлы не преобразуются. Если проверка не прошла, preprocessing не запускается.

Каждая сессия хранится по этапам. Исходные файлы находятся в `original/` и после получения не изменяются. Этапы сохраняют только свои результаты, не копируя всю сессию:

```text
data/
├── raw/{session_id}/
│   ├── archives/            # Received ZIP archives
│   └── original/            # Unpacked phone data
└── processed/{session_id}/
    ├── validation/
    ├── reconstruction/      # point_cloud.ply + mesh.ply
    ├── mesh_preprocessing/
    ├── selected_frames/
    ├── segmentation/
    ├── mask_projection/
    ├── mesh_segmentation/
    └── wall_planes/
```

`--session` указывает на папку сессии, а не на `original`. Вывод обработки внутрь `original` запрещён. Отбор кадров сохраняет ссылки на исходники, фотографии не копируются.

Другую сессию и новую папку результата можно указать явно:

```powershell
python run_geometry.py --session data/processed/47333462 --output data/processed/47333462/mesh_preprocessing-v2 --frames-output data/processed/47333462/selected_frames-v2
```

Существующие результаты не перезаписываются; для повторного запуска задайте новые `--output` и `--frames-output`. `python main.py` по-прежнему запускает HTTP-сервер.

## Отбор кадров для SAM

Если mesh уже обработан, запустите только отбор кадров:

```powershell
python run_geometry.py --session data/processed/47333462 --frames-only
```

`FrameFilterService` читает метаданные, сортирует кадры по времени, проверяет параметры камеры и декодирует RGB. Кадры с отсутствующими данными, повреждёнными изображениями, неверными размерами, низкой резкостью либо слишком низкой/высокой средней яркостью отклоняются. Отсутствующий tracking state в датасете не считается причиной отказа: при наличии корректного pose состояние записывается в результат как неизвестное.

Первый пригодный кадр выбирается всегда. Следующий выбирается при перемещении от **последнего выбранного** кадра минимум на 0.30 м **или** изменении направления взгляда минимум на 15°. Поворот телефона вокруг оптической оси не считается новым ракурсом. Расчёт расстояния и угла использует тот же геометрический подход, что [hloc/pairs_from_poses.py](https://github.com/cvg/Hierarchical-Localization/blob/master/hloc/pairs_from_poses.py); последовательный отбор реализован независимо, установка hloc не нужна.

Резкость — дисперсия четырёхсоседнего оператора Лапласа по яркости 0–255. Картинка для оценки только уменьшается до максимальной стороны 640 пикселей, исходник не изменяется. Начальный порог резкости 20.0, средней яркости — от 10 до 245. Это эвристики, которые нужно подбирать под разрешение и характер съёмки; они не гарантируют отсутствие размытия, засветки или полный обзор комнаты.

Настройки и повторный запуск:

```powershell
python run_geometry.py --session data/processed/47333462 --frames-only --translation-m 0.30 --view-angle-deg 15 --min-sharpness 20 --frames-output data/processed/47333462/selected_frames-v2
```

В `selected_frames.json` находятся выбранные кадры в хронологическом порядке: `frame_id`, `timestamp_s`, пути `rgb` и `metadata` относительно **корня сессии**, оценки качества и причина выбора. `report.json` содержит параметры, счётчики и решение для каждого входного кадра. Если все кадры отклонены, сохраняется отчёт со статусом `failed` и возвращается ошибка. Вывод публикуется целиком после записи обоих файлов; `original` не изменяется.

Вызов из Python:

```python
from geometry_engine.frame_filter import FrameFilterService, FrameFilterSettings

service = FrameFilterService(FrameFilterSettings(min_translation_m=0.3, min_view_angle_deg=15))
report = service.filter("data/processed/47333462")
```

SAM и проверка покрытия mesh этим этапом не запускаются. Пороговый отбор пока сравнивает кадр только с последним выбранным и может сохранять повторный ракурс при возвращении камеры в прежнее место.

## Сегментация через Meta SAM API

`SegmentationService` читает `selected_frames/selected_frames.json`, передаёт RGB в **Meta Model API**, фильтрует полученные маски и сохраняет результат этапа. Используется модель `sam-3.1`, работающая у Meta. Локальные NVIDIA GPU, PyTorch и веса модели не нужны.

За основу взяты [официальный пример запросов Meta](https://dev.meta.ai/docs/sam/segmenting) и [официальная библиотека meta-sam-parser](https://dev.meta.ai/docs/sam/client-libraries). Транспорт — уже используемый в проекте HTTPX. `meta-sam-parser==0.1.0` расшифровывает потоковый ответ и сжатые маски; собственный декодер формата не нужен.

Установка из папки `geometry-engine`:

```powershell
.\myenv\Scripts\python.exe -m pip install -r requirements.txt
```

Добавь ключ Meta в `.env` (пример имени есть в `.env.example`):

```env
MODEL_API_KEY=your-meta-api-key
```

Запуск на первых трёх выбранных кадрах:

```powershell
.\myenv\Scripts\python.exe run_segmentation.py --session data/processed/47333462 --max-frames 3
```

Без `--max-frames` обрабатываются все выбранные кадры. По умолчанию ищем `wall`, `window`, `door`. Meta принимает один концепт на запрос: три кадра и три класса — девять запросов. Фото отправляются непосредственно в Meta как PNG в data URL; публикация фото по общедоступной ссылке не требуется. Размер и ориентация пикселей сохраняются, метаданные EXIF удаляются из отправляемого изображения, чтобы избежать дополнительного поворота.

Для мебели и повторного запуска с новой папкой результата:

```powershell
python run_segmentation.py --session data/processed/47333462 --prompts wall window door chair table bed cabinet --max-frames 3 --output data/processed/47333462/segmentation-v2
```

`--selected-frames` позволяет указать другой `selected_frames.json` внутри сессии. Начальные пороги: `--min-confidence 0.5`, `--min-mask-pixels 64`. Это настраиваемые эвристики, качество распознавания нужно оценивать на реальных кадрах.

Чтобы продолжить после уже сохранённых 50 кадров и обработать оставшиеся:

```powershell
python run_segmentation.py --session data/processed/47333462 --resume
```

`--resume` читает существующий результат и пропускает сохранённые кадры, включая кадры без найденных объектов. Проверяются параметры, модель, исходный manifest, хеши RGB/metadata и наличие масок. При изменении входных данных или настроек продолжение отклоняется до обращения к Meta. Старые маски и наложения сохраняются побайтно; новые результаты добавляются в тот же `segmentation/`. Без `--max-frames` обрабатывается весь оставшийся список. `--resume --max-frames 60` означает довести первые 60 кадров до готовности: при уже готовых 50 будет обработано ещё 10. Если результат лежит в другой папке, укажи её через `--output`.

Через HTTP продолжение включается телом `{"session_id": "47333462", "resume": true}`. При сбое новой партии предыдущий сохранённый результат остаётся доступным. Кадры незавершённой новой партии пока не сохраняются по одному.

```text
data/processed/{session_id}/segmentation/
├── detections.json         # Класс, score, bbox, площадь и ссылка на каждую маску
├── report.json             # Параметры, счётчики, пропущенные маски, исходный manifest
├── masks/{frame_id}/*.png  # Полный размер исходного RGB, значения 0 и 255
└── overlays/{frame_id}.jpg # Фотография с цветными масками и подписями
```

В `detections.json` исходные `rgb`/`metadata` заданы относительно корня сессии, `mask`/`overlay` — относительно папки результата сегментации. `bbox_xyxy` содержит пиксельные границы с исключёнными правой и нижней границами. Маски Meta помещаются на полный холст исходного RGB по координатам возвращённого прямоугольника. Ссылки на метаданные камеры сохраняются для следующего этапа переноса на mesh.

Один `observation_id` — одно наблюдение объекта на конкретном кадре. Дверь на двух фото пока остаётся двумя наблюдениями. Объединение наблюдений и перенос масок на mesh будут отдельным этапом. Нулевое число объектов допустимо и отражается в отчёте; это не доказательство отсутствия стены или двери в комнате.

`original/` и предыдущие этапы не меняются. Результат публикуется целиком после успешной обработки выбранной партии. При ошибке API временные результаты убираются; повторный запуск начинает партию заново. Существующая папка результата не перезаписывается. Базы данных, фоновой очереди и автоматического повтора запросов здесь пока нет.

Через HTTP после `python main.py`:

```http
POST /sessions/process
Content-Type: application/json

{"session_id": "47333462"}
```


Без ключа можно проверить тестами хранение, разбор настоящего формата ответа и HTTP-контракт, но не качество распознавания. Тесты используют подготовленные ответы и не обращаются к Meta. Проверка живого API на наших фото остаётся отдельным шагом после добавления ключа.

Следующие команды выполняются из папки `geometry-engine`:

```powershell
cd geometry-engine
python -m geometry_engine --mesh data/processed/47333462/original/mesh.ply --output data/processed/47333462/mesh_preprocessing-v2
```

Пути ко входному mesh и новой папке результата задаются явно. Основной пакет не выбирает сцену или кадры, не требует `scene.json` и не создаёт проверочные изображения. Координаты должны быть в метрах; автоматического изменения масштаба нет. Существующая папка результата не перезаписывается.

Вызов из будущего сервиса:

```python
from geometry_engine import preprocess_mesh

report = preprocess_mesh("incoming/mesh.ply", "jobs/scan-001/prepared")
```

Для установки пакета из этой папки: `python -m pip install -e .`. Для запуска из папки проекта установка не обязательна, если NumPy и SciPy уже доступны. Зависимости объявлены в `pyproject.toml`; библиотеки рисунков вынесены в дополнительный набор `arkitscenes`.

## Результат preprocessing

В новой папке создаются `mesh.ply`, `geometry.npz` с нормалями и компонентами, `source-mapping.npz`, `spatial-index.npz`, `report.json`. Отдельные части сетки и открытые границы сохраняются. Сглаживание, уменьшение плотности, заполнение отверстий и построение облака из глубины не выполняются.

На нашей сцене результат уже находится в `data/processed/47333462/mesh_preprocessing/`: 439360 вершин и 813462 треугольника. Объединены 64 точных дубликата вершин, удалены 120 вырожденных и 4 повторяющихся треугольника. Положение поверхностей сохранено. Параметры и результаты сохраняются в `report.json`.

## Тестовые инструменты ARKitScenes

Из папки `geometry-engine`:

```powershell
python -m pip install -e ".[arkitscenes]"
python -m tools.arkitscenes.download_scene
python -m tools.arkitscenes.structure_scene
python -m tools.arkitscenes.check_mesh
```

Папка `tools/` исключена из Git и доступна только в локальной рабочей копии. Тесты адаптера находятся в `tools/arkitscenes/tests/`. Эти шаги предназначены для локальной работы с Apple ARKitScenes. Скачивание и структурирование уже выполнены; повторно запускать их для существующих данных не требуется. `check_mesh` проверяет уже подготовленную сетку и пишет отдельный `arkitscenes-check.json` и `mesh-before-after.png`. Для другого результата задайте `--prepared`; для другой структурированной сцены — `--scene`.

Инструменты и их инструкции хранятся локально; они не требуются для работы основного пакета.

## Тесты

```powershell
python -m unittest discover -s tests -v
```

Проверено на Windows с Python 3.14.3. Для новых зависимостей требуется Python 3.11 или новее. Основной пакет использует NumPy, SciPy и Pillow; проверочные графики дополнительно требуют Matplotlib.

## HTTP API

Для сессии, сохранённой приложением отдельными файлами в Supabase:

```http
POST /sessions/extract
Content-Type: application/json

{"project_id":"demo-building","session_id":"d72edb5b-696d-41f6-a2c1-98b09a89d7bd"}
```

Сервис читает `.session.json` и единственный манифест в `projects/{project_id}/sessions/{session_id}/manifests/`, скачивает перечисленные файлы в `data/raw/{session_id}/original/`, сохраняя структуру `observations/`. Размеры и SHA-256 проверяются по манифесту. Каталог публикуется после успешного скачивания всех файлов; результат загрузки — `download-report.json`. Существующая сессия возвращает `409`. ZIP не создаётся. Для этого формата `validation.status` равен `not_run`: адаптация к формату Geometry Engine и геометрическая валидация не входят в загрузку.

Также поддерживается манифест `geometry_capture_parts_v1`: файлы из `parts` скачиваются в `data/raw/{session_id}/archives/`, проверяются по размеру и SHA-256 и распаковываются по отдельности в `original/parts/capture-0001/`, `capture-0002/` и т. д. Файлы из разных ZIP не перезаписывают друг друга. Общие лимиты размера распакованных данных и количества записей действуют на все части вместе. Пути распакованных частей перечислены в `download-report.json`; автоматическое объединение их mesh и геометрическая валидация на этом шаге не выполняются.

Запрос без `project_id` сохраняет прежнее поведение загрузки ZIP, описанное ниже.


```text
geometry_engine_api/
├── dependencies.py         # DI: создание сервиса
└── services/archive.py     # Загрузка и распаковка

geometry_engine/
└── validation.py           # Проверка сцены, mesh и кадров
```

Из папки `geometry-engine`, с активным `myenv`, скопируй `.env.example` в `.env` и заполни настройки:

```env
SUPABASE_URL=https://drzxapqypaxhocrksciq.supabase.co
SUPABASE_STORAGE_BUCKET=inspection-captures
SUPABASE_SECRET_KEY=your-server-secret-key
```

`.env` загружается автоматически; уже установленные переменные окружения имеют приоритет. Ключ остаётся на сервере, `.env` исключён из Git.

```powershell
python main.py
```

Тело запроса:

```json
{
  "session_id": "scan-001"
}
```

После распаковки `SessionValidationService` через DI читает входные данные из `original/`: `capture.json` либо `scene.json` с индексом кадров. Проверяет единицы измерения (метры), читаемость mesh, наличие файлов RGB и указанных depth/confidence, размеры бинарных буферов, параметры камеры и матрицу её положения. Для пригодного кадра обязательны RGB, intrinsics и pose; depth и confidence могут отсутствовать. Проверка не декодирует изображения и не проверяет совмещение камеры с mesh.

Отчёт сохраняется в `data/processed/{session_id}/validation/report.json`. Ответ `201` содержит `session_id`, путь `directory` к корню сессии и `validation`. Неполные кадры отмечаются в отчёте; сцена допускается к дальнейшей работе, если есть пригодные кадры и нет ошибок сцены или mesh. При ошибке валидации возвращается `422` с отчётом; распакованные файлы сохраняются для диагностики. Preprocessing автоматически не запускается.

Файлы остаются после завершения запроса. Существующая папка с таким `session_id` не перезаписывается: возвращается `409`. При ошибке скачивания или распаковки незавершённая сессия не публикуется.

По умолчанию используется `geometry-engine/data`; другой корень можно задать через `GEOMETRY_DATA_DIR`. Сервис ограничивает размер загрузки и распаковки и проверяет пути внутри ZIP.

## Сегментированный mesh

После сохранения масок SAM:

```powershell
python run_mesh_segmentation.py --session data/processed/47333462
```

`MeshSegmentationService` запускает два самостоятельных этапа. `MaskProjectionService` использует intrinsics и pose, находит ближайший треугольник для каждого пикселя и сравнивает его глубину с depth телефона. В пределах масок SAM остаются только геометрически подтверждённые участки. `mask_fusion` сопоставляет наблюдения разных кадров по перекрытию на поверхности и присваивает треугольникам класс и предварительный ID объекта.

В основе — адаптация проверки глубины и сопоставления регионов из [Open3DIS](https://github.com/VinAIResearch/Open3DIS). Используем соответствующие компоненты алгоритма на CPU и треугольниках; полный исследовательский пайплайн Open3DIS и его обученные 3D-признаки не запускаются. Исходные ссылки, зафиксированная версия и лицензия BSD-3-Clause находятся в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Повторных запросов к Meta нет. Новый этап использует существующие `original/`, `mesh_preprocessing/` и `segmentation/`. Для подтверждения метки по умолчанию нужны минимум два разных кадра. Конфликтующие классы, отсутствующая глубина и отсутствующая поверхность остаются `unknown` (ID `-1`, серый цвет). Границы масок сужаются на один пиксель перед переносом. Допуск сравнения глубины — 5 см + 2% расстояния. Эти пороги являются начальными настройками, а не гарантией правильного распознавания.

Результаты:

- `mask_projection/observations.json` и `observations.npz` — наблюдения и индексы соответствующих треугольников; `visibility/` — карты видимости; `report.json` — параметры и хеши входов.
- `mesh_segmentation/mesh.ply` — геометрия подготовленного mesh без изменения координат, порядка вершин и треугольников. На каждой грани сохранены `class_id`, `instance_id`, `confidence` и цвет.
- `mesh_segmentation/labels.npz` — те же метки, уверенность и число подтверждающих кадров по индексам граней.
- `mesh_segmentation/objects.json` — имена классов, предварительные объекты и ссылки на исходные наблюдения.
- `mesh_segmentation/viewer.html` — автономный 3D-просмотр в браузере, переключение классов/объектов и срез комнаты. `preview.jpg` — сравнение RGB и размеченной геометрии из тех же камер.
- `mesh_segmentation/report.json` — покрытие, параметры и происхождение данных.

PLY-просмотрщики, которые игнорируют цвета граней, показывают приближённые цвета вершин. Точные границы видны в `viewer.html`; для следующей обработки используйте `labels.npz` и подготовленную геометрию с тем же порядком граней.

Для изменения порогов объединения уже рассчитанная проекция переиспользуется:

```powershell
python run_mesh_segmentation.py --session data/processed/47333462 --output data/processed/47333462/mesh_segmentation-v2 --min-views 3
```

Хеши входов проверяются перед повторным использованием. Изменившиеся маски, камеры, depth или mesh требуют новой папки `--projection-dir`. Для небольшой проверки задайте `--max-frames 10 --projection-dir data/processed/47333462/mask_projection-check --output data/processed/47333462/mesh_segmentation-check`. Проекция первых десяти кадров не используется для полного запуска. Готовые результаты не перезаписываются; каждый этап публикуется целиком, а завершённая проекция сохраняется, если следующий этап завершился ошибкой.


ID объектов пока обозначают кандидатов на объединение: одна физическая стена может остаться несколькими объектами. Уверенность — оценка согласованности наблюдений, не вероятность правильности. Полная поверхность не достраивается: стекло или проём без треугольников не появятся в mesh. Геометрическая проверка отбрасывает часть неподдержанных масок за окном, но не заменяет определение границ помещения.

## Следующий этап

Границы помещения, пересечения соседних стен и контуры проёмов пока не восстановлены. Найденные ниже плоскости — опора для этих шагов, не готовая модель комнаты.

## Плоскости стен через RANSAC

Из папки `geometry-engine`, с активным окружением:

```powershell
python run_wall_planes.py
```

По умолчанию обрабатывается `data/processed/47333462`. `WallPlaneService` читает `mesh_preprocessing/mesh.ply`, но **использует в RANSAC только треугольники с меткой SAM `wall`** из `mesh_segmentation`. Этот фильтр действует на случайную выборку, подгонку, уточнение и окончательное назначение треугольников. Остальные поверхности показываются во viewer как контекст и не влияют на плоскости, даже если находятся рядом или лежат в той же плоскости. Сохранённые метки обязательны; повторный вызов Meta не нужен.

Алгоритм адаптирован из [Open3D 0.19.0 — SegmentPlane](https://github.com/isl-org/Open3D/blob/1e7b17438687a0b0c1e5a7187321ac7044afe275/cpp/open3d/geometry/PointCloudSegmentation.cpp): случайные тройки точек задают гипотезы, расстояния определяют поддержку, выбранная плоскость уточняется по поддерживающей геометрии. Реализация на NumPy использует выборку поверхности пропорционально площади, проверку нормалей и соответствие исходным треугольникам. Установка Open3D и новые зависимости не требуются. Источник и лицензия указаны в `THIRD_PARTY_NOTICES.md`.

Начальные параметры: допуск 25 мм, отклонение нормали треугольника до 30°, наклон стены от вертикали до 12°, поддерживающая площадь от 1 м², размеры области поддержки от 0.5 × 1 м. Случайная выборка воспроизводима с фиксированным seed. Параллельность и прямые углы между стенами не навязываются.

Результат в `data/processed/{session_id}/wall_planes/`:

- `planes.json` — нормаль и смещение каждой плоскости в исходных мировых координатах, площадь поддержки, отклонения, четыре угла отображаемого прямоугольника и статус кандидата.
- `face_labels.npz` — `face_plane_id` для каждого треугольника подготовленного mesh; `-1` означает отсутствие назначенной плоскости. Порядок граней сохранён.
- `viewer.html` — исходный mesh и полупрозрачные плоскости; выбор отдельной плоскости, переключение цветов треугольников, прозрачности и среза.
- `preview.png` — статическое сравнение mesh и наложения.
- `report.json` — параметры, хеши входов, счётчики и происхождение оси вверх.

В viewer одинаковый цвет связывает плоскость с её поддерживающими треугольниками. Все кандидаты имеют статус `wall_candidate` и опираются только на метки `wall`. Нераспознанные участки стены автоматически не присоединяются; ошибки самого SAM также могут повлиять на результат. RMSE показывает прилегание выбранных треугольников к плоскости, **не точность размеров помещения**.

Прямоугольники показывают пределы найденных фрагментов. Они могут визуально проходить через отверстия и закрытые мебелью участки, но не добавляют геометрию в mesh. Размеры прямоугольников не считаются длиной и высотой полной стены. Проёмы не заполняются и не восстанавливаются этим шагом.

Повторный запуск с другим допуском сохраняется отдельно:

```powershell
python run_wall_planes.py --session data/processed/47333462 --distance-m 0.03 --output data/processed/47333462/wall_planes-v2
```

Мировая ось вверх берётся из известного формата: Z для нашего ARKitScenes, Y для нативного контракта ARKit. Для других систем координат укажите `--up-axis x`, `y` или `z`. Исходные файлы и предыдущие этапы не изменяются; существующая папка результата не перезаписывается. Новый HTTP endpoint для экспериментального этапа не добавлен.
## Реконструкция из сырых кадров (Open3D, CPU)

Самостоятельный `ReconstructionService` принимает папку сессии с RGB, глубиной,
confidence и параметрами камер. Мобильный `mesh.ply` не используется.
Поддерживаются `original/capture.json` и multipart-структура
`original/parts/capture-000N/capture.json`, как в сессии
`1f62c1aa-f6fa-4543-8851-32ee73365abf`.

Из папки `geometry-engine`:

```powershell
.\myenv\Scripts\python.exe -m pip install -r requirements-reconstruction.txt
.\myenv\Scripts\python.exe run_reconstruction.py --session data/processed/1f62c1aa-f6fa-4543-8851-32ee73365abf
```

С активированным окружением достаточно `python run_reconstruction.py --session data/processed/ID`.

Результаты сохраняются отдельно:

```text
data/processed/ID/reconstruction/
    point_cloud.ply   # Цветные точки поверхности, извлечённые из общей TSDF
    mesh.ply          # Треугольники и цвета, совместимые с mesh preprocessing
    cameras.json      # Камеры, ссылки на исходные metadata и статусы кадров
    report.json       # Параметры, принятые/пропущенные кадры, статистика
    viewer.html       # Локальный интерактивный просмотр без CDN
    preview.png       # Вид сверху и под углом
```

Данные остаются в исходных мировых координатах ARKit, в метрах, с вертикальной
осью Y. Сервис масштабирует intrinsics под depth, переводит оси камеры ARKit
в right/down/forward и передаёт Open3D обратную матрицу camera-to-world.
RGB и depth должны сохраняться в согласованной ориентации сенсора.

По умолчанию размер вокселя 2 см, диапазон глубины 0.15–5 м, confidence >= 1.
Недостоверные пиксели исключаются. Кадры с недостаточной глубиной, ненормальным
tracking state или явным конфликтом с несколькими предыдущими видами пропускаются.
Проверка конфликта эвристическая: допуск 5 см + 2% глубины, не гарантия точности
измерений. Неперекрывающиеся виды допускаются. Ограничения на паузу между кадрами нет.
Пропуски покрытия и стекло могут оставить отверстия; сервис не достраивает их,
не исправляет poses автоматически и не восстанавливает скрытые границы стен.

Параметры: `--voxel-size`, `--sdf-trunc`, `--max-depth`, `--min-confidence`,
`--exclude-frame UUID` (повторяемый), `--keep-conflicting-frames`, `--no-preview`.
Существующий результат не перезаписывается. Для эксперимента укажите новую папку:
`--output data/processed/ID/reconstruction-v2`. Исходные файлы не изменяются.

Вызов из будущего worker или HTTP endpoint:

```python
from geometry_engine.reconstruction import ReconstructionService
report = ReconstructionService().reconstruct("data/processed/ID")
```

В существующий HTTP endpoint сервис автоматически не подключён: скачивание и
реконструкция запускаются независимо. Дальнейший preprocessing может принимать
`reconstruction/mesh.ply`; адаптация остальных стадий к multipart-кадрам остаётся
отдельной задачей.

### Wall grouping versus flatness

Wall candidates are grouped using orientation, nearby support and local surface continuity.
The 95% within 3 cm criterion now records a flatness warning instead of rejecting a group.
Bidirectional nearest surface samples measure normal separation (90th percentile).
Separation >= 1.5 cm is rejected unless the patches share >= 10 cm of exact mesh edges
and local separation remains below 3 cm. Every neighboring original pair is checked.
These are configurable geometric heuristics, not proof that a physical step is absent;
sub-threshold steps or reconstruction artifacts remain ambiguous.
No vertices are moved or discarded. `planes.json` includes constituent area fractions,
area-weighted P95 of triangle maximum vertex distances, maximum distance and a warning.
The common plane is a reference for measuring deviations, not recovered hidden geometry.
