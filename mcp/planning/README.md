# Planning MCP

Планирование строительных проектов: CSV работ превращается в расписание
с датами, ресурсами и подрядчиком (движок — библиотека SAMPO).
Предназначен только для планирования обустройства нефтегазовых
месторождений, ни для чего больше.

Контейнер слушает `0.0.0.0:8080/mcp`, наружу торчит `127.0.0.1:18082`. Образ `python:3.10-slim`.

## Структура

Конвейер `schedule_project` идёт по адаптерам строго в этом порядке:

| #  | Файл                                        | Ответственность                                                                                                                                                                             |
|----|---------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1  | `src/planning_adapter/models.py`            | Pydantic-контракт: запрос/результат, алгоритмы, цели, размеры                                                                                                                               |
| 2  | `src/planning_adapter/config.py`            | Только константы: границы, дефолты, 23 кода `E_*`                                                                                                                                           |
| 3  | `src/planning_adapter/source_loader.py`     | Загрузка `csv_url` (HTTPS с пиннингом / S3), SSRF-guards, лимиты                                                                                                                            |
| 4  | `src/planning_adapter/input_loader.py`      | CSV-текст → `PlanningRow[]`: канон 7 колонок (`activity_id`, `activity_name`, `volume`, `measurement` обязательны; `structure`, `edges`, `granular_unit` опциональны), вложенные ячейки     |
| 5  | `src/planning_adapter/storage_adapter.py`   | Read-only граница к admin-БД (`RM_ADAPTER_CONN_STR`); держит до 4 подключений с вытеснением давно неиспользуемых (LRU)                                                                      |
| 6  | `src/planning_adapter/resource_adapter.py`  | Совмещает два заимствованных контракта: хранилище требует `category`, ядро `ResTimeModel` её не принимает — адаптер подставляет категорию через приватное view, не меняя заимствованный код |
| 7  | `src/planning_adapter/estimator_adapter.py` | Ленивый двойник бэкендного `FieldDev` (SAMPO `WorkTimeEstimator`)                                                                                                                           |
| 8  | `src/planning_adapter/graph_adapter.py`     | Строгая валидация (лимиты → дубли → рёбра → предшественники → циклы) + `WorkGraph`                                                                                                          |
| 9  | `src/planning_adapter/scheduler_adapter.py` | 5 алгоритмов → классы SAMPO, подрядчик, `SchedulingPipeline`                                                                                                                                |
| 10 | `src/planning_adapter/service.py`           | Единственная точка входа: склейка 1–9 + конвертация в `ScheduleResult`                                                                                                                      |
| 11 | `src/planning_mcp/server.py`                | Один `@mcp.tool`: ограничение параллельных расчётов и предельное время выполнения                                                                                                           |

`sources/` — нетронутые снимки (`field_dev.py`, `stairs-resource-model`, `stairs-storage`); `src/stairs_*` —
заимствованный
runtime. Собственный код — только `planning_adapter` + `planning_mcp`.

Инструмент один: `schedule_project(csv_url, algorithm, optimization_objective, genetic_generations?, deadline?)` →
`ScheduleResult` (работы с датами/ресурсами + рёбра, в порядке входа).

## Настройка

Env (пусто = fail-closed отказ, DSN никогда не логируется):

| Переменная                        | Назначение                                   |
|-----------------------------------|----------------------------------------------|
| `RM_ADAPTER_CONN_STR`             | DSN доверенной admin-БД моделей              |
| `PLANNING_CSV_ALLOWED_HOSTS`      | Allowlist HTTPS-хостов CSV                   |
| `PLANNING_CSV_ALLOWED_S3_BUCKETS` | Allowlist S3-бакетов CSV                     |
| `PLANNING_MAX_CONCURRENT`         | Параллельных расчётов (дефолт 2, потолок 16) |
| `PLANNING_DEADLINE_SECONDS`       | Дедлайн расчёта (дефолт 300, потолок 3600)   |
| `PORT`                            | Порт внутри контейнера (дефолт 8080)         |

Кодовые лимиты (`config.py`): 200 строк, 16 рёбер/структур на строку, поколения genetic 1–200 (дефолт SAMPO 50);
скачивание 10 МБ / 15 с / 3 редиректа. Дефолты: `heft` / `min_time` / `mid` / `standard`. Скейлеры подрядчиков
`min=1, mid=5, max=10` — зеркало бэкенда (`_SIZES_TO_FACTOR`). Флаги конвейера (
`RESTORE=False, REFILL/RESTRUCTURE=True, ALL_CONNECTIONS=True`) заменяют per-project настройки диалога бэкенда, у tool
такого входа нет.

Алгоритмы: `topological`, `randomized_topological`, `heft`, `heft_between`, `genetic` (Serial SGS). Цели: `min_time`,
`deadline` (только genetic + дата ≥ старта), `resource_optimization`. Не-genetic принимает только `min_time`. `SF`
проходит вход ради CSV-совместимости и режется в графе (`E_EDGE_INVALID`) — у SAMPO типа старт-финиш нет.

Модели БД: сначала декларативный JSON `planning-model/v1`, fallback — legacy pickle **только** из доверенной admin-БД (
pickle ограничен модулем `stairs_resource_model/res_time_model.py`).

## Обновление

**Изменился исходник:** заменить снимок в `sources/`, обновить `SOURCES.md`; заимствованные файлы не править.
Единственный
ручной перенос из бэкенда — `estimator_adapter.py` (двойник `FieldDev` без import-time модели и `config/DAL`).

**Нужны изменения в самом MCP:** править `planning_adapter`/`planning_mcp`; новые лимиты и коды — только в
`config.py` (+ кортеж `ERROR_CODES`). Порядок валидации в графе — C4, не менять молча.

**Зависимости:** `requirements.txt` — `mcp[cli]==1.16.0`, `pydantic==2.11.9`, `sampo==0.1.2.0.6`.

## Сценарии использования

Выполнение — через Inspector на `http://127.0.0.1:18082/mcp`. Живые вызовы
требуют MinIO-бакет с `plan.csv` и сидированную базу моделей
(см. `mcp/TESTING.md` §0–2).

- **Базовое расписание.** Tool `schedule_project`, аргументы:
  `{"csv_url": "s3://test/plan.csv", "algorithm": "heft", "optimization_objective": "min_time"}`.
  Ответ — 5 работ `A1..A5` с датами начала/конца, длительностями и ресурсами.
  Контрольная цифра сида: `A2 = 25.0` дней.
- **Генетическая оптимизация.** Тот же вызов с `"algorithm": "genetic"` и
  `"genetic_generations": 2`. Формат ответа тот же, длительности иные. Цель
  `"deadline"` требует дату (`"deadline": "2026-12-31"`), цель
  `"resource_optimization"` даёт более длинное расписание с ровной загрузкой ресурсов.
- **Ошибки без тяжёлых вычислений.** Связь `SF` в CSV — `E_EDGE_INVALID`;
  поколения без `genetic` или цель `deadline` без даты — `E_OPTIONS_INVALID`;
  повторный `activity_id` — `E_DUPLICATE_ID row 2`; отсутствие строки подключения
  к базе — `E_RESOURCE_MODEL_MISSING`. Поле `warnings` в ответе всегда пустое —
  по дизайну.

## Проверка

- L1: `compose up` + `scripts/smoke.py` (1 инструмент).
- L2: `pytest mcp/planning/tests -q -p no:cacheprovider` (Python 3.10).
- L3: Inspector, `http://127.0.0.1:18082/mcp` (нужны MinIO-бакет и сидированная БД — см. `mcp/TESTING.md`).
