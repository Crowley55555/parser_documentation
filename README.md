# parser_documentation

Парсит документацию (скрины/страницы) со Scribd и сохраняет в папку изображений и в PDF.

## Установка

```bash
pip install -r requirements.txt
# Если будете использовать парсинг по URL (--url):
playwright install chromium
```

## Использование

### Вариант 1: по URL страницы Scribd

Скрипт откроет страницу в headless-браузере, дождётся `document_container` и вытащит изображения:

```bash
python parse_scribd_document.py --url "https://ru.scribd.com/document/671620306/Ps-Cncsxy800-Manual" --out output
```

### Вариант 2: из сохранённого HTML

1. Откройте документ на Scribd в браузере.
2. Прокрутите документ, чтобы подгрузились нужные страницы (скрины).
3. В DevTools (F12) найдите элемент с классом `document_container`, ПКМ → Copy → Copy outerHTML и сохраните в файл, например `scribd_page.html`.
4. Запустите:

```bash
python parse_scribd_document.py --html scribd_page.html --out output
```

Результат: в папке `output` появятся файлы `001.png`, `002.jpg`, ... и один PDF `documentation.pdf` (или `*_documentation.pdf` при использовании `--html`).

Только изображения без PDF: добавьте флаг `--no-pdf`.
