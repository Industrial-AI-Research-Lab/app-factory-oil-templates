# Экспорт структурированных результатов

`render_report(input_path, template_id, template_version, formats, output_dir)`
принимает JSON и возвращает manifest с абсолютным `manifest_path`.
Семейства: `news`, `knowledge`; версии: `1.0.0`, `1.1.0`.
Форматы задаются явно: `txt/json/csv/html/docx/xlsx/pdf`.

Обязательные строки: `project_id`, `run_id`, `result_id`, `result_version`,
`title`, `answer`. Обязательные массивы: `tables`, `sources`, `limitations`.
Таблица: `{id,title,columns,rows}`; колонка: `{key,label,type,unit}`;
`type` — `string`, `number` или `boolean`, `unit` — строка или null.
Строка: `{values:{key:typed_value_or_null},source_ids:[source_id]}`.
Источник: `{id,title,url}`; URL — строка или null. Ограничения — строки.
`metadata` для knowledge: `{question,schema_version,collection_version}`;
для news: `{topic,period,conditions,publication_count,event_count,categories}`,
где counts — переданные целые >=0, categories — массив строк.
Никаких вычислений предметной области. Null отображается «Нет данных»;
JSON сохраняет null и числовые типы. XLSX сохраняет числа числами, текст — текстом.
XLSX отклоняется с ошибкой render, если ячейка длиннее 32767 символов или число
не проходит точную проверку после сохранения и повторного открытия. Другие
форматы продолжают формироваться; числовые значения не округляются незаметно.

Каталог: `output_dir/project_id/run_id/export_uuid/`. Исходные байты неизменно
сохраняются в `report-data.json`. Каждый повторный экспорт имеет новый UUID.
Manifest содержит export/project/run ID, result `{id,version,sha256}`, template
`{id,version}`, formats `{format:{status,filename,mime_type,size_bytes,sha256,error?}}`.
Ошибки содержат `{code,stage,message}`. `generated` означает проверенный локальный
файл; `ready` выставляет publisher после регистрации и проверки скачивания.
Неизвестные форматы/шаблоны и неверные данные отклоняются до записи экспорта
через `ReportError(code,stage,details)`. `retry_report(manifest_path)` повторяет
только ошибки render, используя сохранённые данные и шаблон, под блокировкой.

Зависимости контейнера: Jinja2, python-docx, openpyxl, WeasyPrint и pypdf; для тестов
pytest. Нужны системные библиотеки WeasyPrint и шрифт DejaVu Sans.
Внутри контейнера, из `experiments/deep-domain-research`:
`python -m pytest reporting/tests -q`. На хосте генератор и тесты не запускаются.
CLI генерации: `python -m reporting render reporting/tests/fixtures/news.json --template-id news --template-version 1.0.0 --formats html csv pdf json --output-dir /tmp/reports`.
Повтор: `python -m reporting retry /tmp/reports/project/run/export/export-manifest.json`.
stdout — JSON manifest либо структурированная ошибка; exit 0 — все форматы
успешны, 1 — частичный сбой, 2 — ошибка вызова/валидации. stderr — диагностика.
HTML/PDF используют одну HTML-разметку со встроенным CSS; DOCX/XLSX используют
версионированные JSON presets оформления и собственные библиотеки форматов.
Маршрут выдачи реализован, но живая приёмка B05 пока не пройдена: см.
[протокол](../../../tasks/ipr4-demo/b05/template-export-check.md).

## Подготовка контейнера

Пакет доставляется отдельным вложением, собранным оператором командой
`python tasks/ipr4-demo/b05/package.py b05-delivery-current.py`. В архив входят
только `reporting/`, его фиксированные шаблоны и синтетические тестовые входы;
контрольная разметка доменных корпусов не передаётся. Штатные
`attachment_list` → `attachment_fetch` → `bash python3 <полученный путь>`
распаковывают пакет в `/workdir/b05`.

До показа подготовьте зависимости отдельными командами Container-Use:

```sh
python3 /workdir/b05/reporting/setup.py apt-index
python3 /workdir/b05/reporting/setup.py pango
python3 /workdir/b05/reporting/setup.py fonts
python3 /workdir/b05/reporting/setup.py pdf-tools
python3 /workdir/b05/reporting/setup.py office
python3 /workdir/b05/reporting/setup.py python
python3 /workdir/b05/reporting/setup.py tests
python3 /workdir/b05/reporting/setup.py check
env PYTHONPATH=/workdir/b05 python3 -m pytest /workdir/b05/reporting/tests -q
```

`check` ничего не устанавливает. Генерация и повторный экспорт также не
устанавливают зависимости. Длинная установка в проверенном внешнем prod
упёрлась в лимит инструмента 30 секунд; это не подтверждение успешной установки.
После неопределённого timeout сначала проверьте фактическое состояние контейнера.

## Публикация оригинальных файлов

`await publish_report(manifest_path, client, reverify=False)` принимает
аутентифицированный `httpx.AsyncClient` для выбранного AppFactory API. Логин и токен
передаются транспортом оператора; библиотека не хранит секреты. Рендер не
импортируется и не вызывается публикацией.

Состояния: `generated` → загрузка и регистрация штатным multipart
`POST /api/projects/{project_id}/messages?run_id=…` → получение
`GET /api/projects/{project_id}/attachments/{id}?raw=1` → `ready` только при
совпадении MIME, размера и SHA256. Ошибки транспорта имеют `stage=publish`.
Точки входа/выхода и все оригинальные файлы сохраняются при частичном сбое.

Перед загрузкой проверяются SHA256 исходного JSON и каждого локального файла.
Имена вложений содержат `export_id`. Повтор ищет уже зарегистрированное имя
в списке **своего проекта**; потеря HTTP-ответа не должна создавать второе
вложение. Два одновременных вызова для одного каталога сериализуются общей с
рендером блокировкой `.export.lock`: второй получает `EXPORT_BUSY`.
После аварийного завершения процесса блокировку снимает оператор, только
убедившись, что прежний процесс больше не работает. Два независимых копирования
каталога на разные машины не разделяют эту блокировку и не поддерживаются как
одновременные публикующие процессы.

Готовые форматы при обычном повторе пропускаются. `reverify=True` повторяет
пользовательское скачивание, например после нового входа OIL. Ошибки этапа
`render` исправляются через `retry_report`, затем снова вызывается публикация.
Ни один из этих повторов не запускает доменное исследование.

Для оператора предусмотрены транспортные скрипты в `tasks/ipr4-demo/b05/`:

1. В контейнере `python3 /workdir/b05/reporting/bridge.py <каталог экспорта>`
   выдаёт ZIP с оригинальными байтами и хешами через полный tool result.
2. `receive.py <полный-tool-result.json> <локальная-копия>` сверяет ZIP и каждый
   файл; не генерирует и не преобразует документы.
3. `publish_files.py <manifest> --post-summary` передаёт эти байты через
   существующий OIL API и проверяет скачивание. `--reverify` повторяет проверку
   после нового логина, `--local` явно выбирает внутренний адрес того же prod.

Эта операторская передача оригинальных байтов соответствует транспорту B04;
генератор, доменные команды и тесты исполняются только в контейнере. Для запуска
публикации целиком из контейнера передайте тот же аутентифицированный HTTP-клиент
через окружение оператора. Нового сервиса или серверного endpoint здесь нет.

Файлы появляются в **User Attachments** и в карточках вложений сообщения Chat.
Карточки используют авторизованный `openPresignedDownload` и получают свежую
короткую ссылку при каждом скачивании. Обычный Markdown-переход на raw API не
передаёт Bearer, поэтому `chat_summary(manifest, report_data)` даёт результат,
имена файлов и маршрут через карточки/Artifacts; raw пути остаются в manifest
для API-клиента. Подписанные URL в Chat и manifest не сохраняются.

## Передача N04 и G05

N04 собирает утверждённые события, основания и source ID в этот JSON, затем
вызывает `news` с `html csv pdf json`. G05 собирает ответ и факты с единицами,
условиями и версиями схемы/коллекции, затем вызывает `knowledge` с
`txt docx xlsx pdf html json`. Оба вызывают один `render_report` и один publisher.
Данные подграфа и его интерактивность добавляет G05 в предусмотренный HTML-блок.
SQLite и другие файлы коллекции пока не включены в контракт report-data: их
связывание с тем же механизмом вложений остаётся G05.

Контрольные входы `tests/fixtures/`: `small.json`, `news.json`, `empty.json`,
`long.json`. `tests/live_check.py` требует явные project/run ID. Он создаёт
самостоятельные комплекты из синтетических данных; это не результаты N02/G04.
`visual_check.py <pdf|docx|xlsx> <новый-каталог-QA>` открывает файлы профильными
конвертерами и сохраняет PNG страниц. Создание PNG само по себе не закрывает
визуальную проверку: страницы необходимо просмотреть.

CSV: текст с начальным =, +, -, @ после удаления ведущих пробелов либо с начальным tab/CR получает префикс апострофа для безопасного открытия в таблицах. Числовые значения, включая отрицательные, не получают этот префикс; исходный JSON остаётся побайтово прежним.
