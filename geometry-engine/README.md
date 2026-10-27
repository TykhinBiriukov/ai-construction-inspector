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

## Следующий этап

Автоматическая сегментация RGB с SAM, перенос масок на видимые треугольники mesh, объединение наблюдений объектов и определение плоскостей и наблюдаемых границ. Эти этапы ещё не реализованы.
