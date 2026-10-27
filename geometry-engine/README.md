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

Следующие команды выполняются из папки `geometry-engine`:

```powershell
cd geometry-engine
python -m geometry_engine --mesh data/structured/47333462/mesh.ply --output data/structured/47333462/mesh_preprocessing-v2
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

Один endpoint: `POST /sessions/extract`. Тонкий API-слой передаёт `session_id` сервису через FastAPI `Depends`. Сервис скачивает ZIP по адресу `{GEOMETRY_STORAGE_URL}/{session_id}` и распаковывает его в `data/{session_id}`. Имя файла в хранилище совпадает с `session_id`; расширение автоматически не добавляется. Preprocessing mesh на этом шаге не запускается.

```text
geometry_engine_api/
├── app.py                  # Endpoint и модели запроса/ответа
├── dependencies.py         # DI: создание сервиса
└── services/archive.py     # Загрузка и распаковка
```

Из папки `geometry-engine`, с активным `myenv`:

```powershell
$env:GEOMETRY_STORAGE_URL = "https://your-storage.example/scans"
python -m uvicorn geometry_engine_api.app:app --host 127.0.0.1 --port 8000
```

Тело запроса:

```json
{
  "session_id": "scan-001"
}
```

Ответ `201` содержит `session_id` и путь `directory` к сохранённой папке. Файлы остаются после завершения запроса. Существующая папка с таким `session_id` не перезаписывается: возвращается `409`. При ошибке скачивания или распаковки незавершённая сессия не публикуется.

По умолчанию используется `geometry-engine/data`; другой корень можно задать через `GEOMETRY_DATA_DIR`. `GEOMETRY_STORAGE_URL` задаёт общий URL папки в хранилище. Сервис ограничивает размер загрузки и распаковки и проверяет пути внутри ZIP. Содержимое архива пока не проверяется как mesh или frames — это отдельный следующий шаг.

## Следующий этап

Автоматическая сегментация RGB с SAM, перенос масок на видимые треугольники mesh, объединение наблюдений объектов и определение плоскостей и наблюдаемых границ. Эти этапы ещё не реализованы.
