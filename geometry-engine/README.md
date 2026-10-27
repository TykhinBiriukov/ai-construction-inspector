# Geometry Engine

Основной код обрабатывает готовую треугольную сетку в метрах. Он доступен как Python-пакет `geometry_engine` для подключения к сервису. Подготовка данных Apple ARKitScenes и диагностические картинки отделены в `tools/arkitscenes/`.

## Структура

```text
geometry-engine/
├── geometry_engine/          # Основная логика
│   ├── preprocessing.py      # Обработка mesh и сохранение результата
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

Для последовательного запуска проверки сессии и preprocessing mesh:

```powershell
python run_geometry.py
```

По умолчанию используются `data/syntethic_data` и новая папка результата `data/syntethic_data/mesh_preprocessing`. Поддерживаются входные форматы `capture.json` (контракт телефона v1) и `scene.json` с `frames.jsonl` (structured). Исходные файлы не преобразуются. Если проверка не прошла, preprocessing не запускается.

Каждая сессия хранится по этапам. Исходные файлы находятся в `original/` и после получения не изменяются. Этапы сохраняют только свои результаты, не копируя всю сессию:

```text
data/{session_id}/
├── original/               # capture.json или scene.json, mesh.ply, frames/
├── validation/report.json  # Результат проверки
└── mesh_preprocessing/     # Обработанный mesh, индексы и report.json
```

`--session` указывает на папку сессии, а не на `original`. Вывод preprocessing внутрь `original` запрещён. Будущий отбор кадров будет сохранять только список в `selected_frames/`; этот этап пока не реализован.

Другую сессию и новую папку результата можно указать явно:

```powershell
python run_geometry.py --session data/structured/47333462 --output data/structured/47333462/mesh_preprocessing-v2
```

Существующий результат не перезаписывается; для повторного запуска задайте новый `--output`. `python main.py` по-прежнему запускает HTTP-сервер.

Следующие команды выполняются из папки `geometry-engine`:

```powershell
cd geometry-engine
python -m geometry_engine --mesh data/structured/47333462/original/mesh.ply --output data/structured/47333462/mesh_preprocessing-v2
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

На нашей сцене результат уже находится в `data/structured/47333462/mesh_preprocessing/`: 439360 вершин и 813462 треугольника. Объединены 64 точных дубликата вершин, удалены 120 вырожденных и 4 повторяющихся треугольника. Положение поверхностей сохранено. Параметры и результаты сохраняются в `report.json`.

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

Проверено на Windows с Python 3.14.3. Для новых зависимостей требуется Python 3.11 или новее. Основной пакет использует NumPy и SciPy; только проверочные инструменты изображений требуют Pillow и Matplotlib.

## HTTP API

Один endpoint: `POST /sessions/extract`. Тонкий API-слой передаёт `session_id` сервису через FastAPI `Depends`. Клиент Supabase также передаётся через DI. Сервис получает временную ссылку через SDK, скачивает ZIP частями на диск и распаковывает его в `data/{session_id}/original`. Имя ZIP-файла в бакете: `{session_id}.zip`. Например, `session_id: "test"` загружает `test.zip` в папку `data/test/original`. В ZIP по-прежнему лежат сами исходные файлы без обёртки `original/` — её создаёт сервер. Preprocessing mesh на этом шаге не запускается.

```text
geometry_engine_api/
├── app.py                  # Endpoint и модели запроса/ответа
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

Отчёт сохраняется в `data/{session_id}/validation/report.json`. Ответ `201` содержит `session_id`, путь `directory` к корню сессии и `validation`. Неполные кадры отмечаются в отчёте; сцена допускается к дальнейшей работе, если есть пригодные кадры и нет ошибок сцены или mesh. При ошибке валидации возвращается `422` с отчётом; распакованные файлы сохраняются для диагностики. Preprocessing автоматически не запускается.

Файлы остаются после завершения запроса. Существующая папка с таким `session_id` не перезаписывается: возвращается `409`. При ошибке скачивания или распаковки незавершённая сессия не публикуется.

По умолчанию используется `geometry-engine/data`; другой корень можно задать через `GEOMETRY_DATA_DIR`. Сервис ограничивает размер загрузки и распаковки и проверяет пути внутри ZIP.

## Следующий этап

Автоматическая сегментация RGB с SAM, перенос масок на видимые треугольники mesh, объединение наблюдений объектов и определение плоскостей и наблюдаемых границ. Эти этапы ещё не реализованы.
