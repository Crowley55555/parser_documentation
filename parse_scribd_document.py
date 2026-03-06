#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер документации из Scribd (контейнер outer_page_container, блоки outer_page_1 … outer_page_N).
Если в контейнере есть ссылка на PDF — скачивает его как есть. Иначе собирает один PDF из изображений и текста.
"""

import re
import sys
import argparse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# опционально: сборка PDF
try:
    import img2pdf
    HAS_IMG2PDF = True
except ImportError:
    HAS_IMG2PDF = False


def _image_url_from_node(node) -> str | None:
    """Из одного узла извлекает URL изображения: <img src> или background-image в style."""
    img = node.find("img")
    if img:
        src = (img.get("src") or img.get("data-src") or "").strip()
        if src and not src.startswith("data:"):
            return src
    bg_re = re.compile(r"background-image:\s*url\(['\"]?([^'\")\s]+)['\"]?\)")
    if node.get("style"):
        m = bg_re.search(node["style"])
        if m:
            u = m.group(1).strip()
            if u and not u.startswith("data:"):
                return u
    for tag in node.find_all(style=True):
        style = tag.get("style", "")
        m = bg_re.search(style)
        if m:
            u = m.group(1).strip()
            if u and not u.startswith("data:"):
                return u
    return None


def _text_from_node(node) -> str:
    """Из одного блока страницы извлекает весь текст (span'ы текстового слоя Scribd, без script/style)."""
    node_copy = BeautifulSoup(str(node), "html.parser")
    for tag in node_copy.find_all(["script", "style"]):
        tag.decompose()
    text = node_copy.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_pdf_url_from_html(html: str) -> str | None:
    """Ищет в контейнере документа ссылку на PDF (a[href], embed[src], iframe[src], data-*). Возвращает URL или None."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one('div[role="document"].outer_page_container') or soup.select_one(
        'div[role="document"][class*="outer_page_container"]'
    )
    if not container:
        container = soup.select_one(".outer_page_container") or soup.find(attrs={"role": "document"}) or soup
    pdf_re = re.compile(r"\.pdf(\?|$|#)", re.I)
    for tag in container.find_all("a", href=True):
        url = (tag.get("href") or "").strip()
        if url and pdf_re.search(url) and not url.startswith("data:"):
            return url
    for tag in container.find_all(["embed", "iframe"]):
        url = (tag.get("src") or "").strip()
        if url and pdf_re.search(url) and not url.startswith("data:"):
            return url
    for tag in container.find_all(attrs={"data-pdf": True}):
        url = (tag.get("data-pdf") or "").strip()
        if url and not url.startswith("data:"):
            return url
    for tag in container.find_all(attrs={"data-src": True}):
        url = (tag.get("data-src") or "").strip()
        if url and pdf_re.search(url):
            return url
    return None


def extract_pages_data_from_html(html: str) -> list[dict]:
    """
    Парсит контейнер <div role="document" class="outer_page_container"> и блоки
    <div class="outer_page only_ie6_border" id="outer_page_1"> … id="outer_page_N">.
    Возвращает список словарей по страницам: [{"image_url": str|None, "text": str}, ...] в порядке id.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one('div[role="document"].outer_page_container') or soup.select_one(
        'div[role="document"][class*="outer_page_container"]'
    )
    if not container:
        container = soup.select_one(".outer_page_container") or soup.find(attrs={"role": "document"}) or soup

    id_re = re.compile(r"^outer_page_(\d+)$")
    page_divs = container.find_all(id=id_re)
    if not page_divs:
        page_divs = container.select(".outer_page.only_ie6_border")
    if not page_divs:
        page_divs = container.find_all(class_=lambda c: c and "outer_page" in (c if isinstance(c, list) else [c]) and "only_ie6_border" in (c if isinstance(c, list) else [c]))

    def sort_key(div):
        mid = id_re.match(div.get("id") or "")
        return (int(mid.group(1)), 0) if mid else (9999, id(div))

    page_divs = sorted(page_divs, key=sort_key)

    pages_data = []
    for div in page_divs:
        pages_data.append({
            "image_url": _image_url_from_node(div),
            "text": _text_from_node(div),
        })
    return pages_data


def extract_images_from_html(html: str) -> list[str]:
    """Список URL изображений по страницам (из того же контейнера, что и текст)."""
    pages = extract_pages_data_from_html(html)
    return [p["image_url"] for p in pages if p["image_url"]]


def download_images(urls: list[str], out_dir: Path, session: requests.Session | None = None) -> list[Path]:
    """Скачивает изображения по порядку, сохраняет как 001.png, 002.png, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    saved = []
    for i, url in enumerate(urls, start=1):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            # определяем расширение по Content-Type или по URL
            ext = ".png"
            ct = (r.headers.get("Content-Type") or "").lower()
            if "jpeg" in ct or "jpg" in ct or ".jpg" in url.lower():
                ext = ".jpg"
            elif "webp" in ct or ".webp" in url.lower():
                ext = ".webp"
            name = f"{i:03d}{ext}"
            path = out_dir / name
            path.write_bytes(r.content)
            saved.append(path)
            print(f"  [{i}/{len(urls)}] {name}")
        except Exception as e:
            print(f"  Ошибка {url[:60]}...: {e}", file=sys.stderr)
    return saved


def download_page_images(pages_data: list[dict], out_dir: Path, session: requests.Session | None = None) -> list[Path | None]:
    """Скачивает по одному изображению на страницу. Возвращает список длиной len(pages_data): Path или None."""
    out_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    result: list[Path | None] = []
    for i, page in enumerate(pages_data, start=1):
        url = page.get("image_url")
        if not url:
            result.append(None)
            continue
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            ext = ".png"
            ct = (r.headers.get("Content-Type") or "").lower()
            if "jpeg" in ct or "jpg" in ct or ".jpg" in url.lower():
                ext = ".jpg"
            elif "webp" in ct or ".webp" in url.lower():
                ext = ".webp"
            path = out_dir / f"{i:03d}{ext}"
            path.write_bytes(r.content)
            result.append(path)
            print(f"  [{i}/{len(pages_data)}] {path.name}")
        except Exception as e:
            print(f"  Ошибка страница {i}: {e}", file=sys.stderr)
            result.append(None)
    return result


def images_to_pdf(image_paths: list[Path], pdf_path: Path) -> None:
    """Собирает список изображений в один PDF."""
    if not HAS_IMG2PDF:
        raise RuntimeError("Установите img2pdf: pip install img2pdf")
    with open(pdf_path, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in image_paths]))
    print(f"PDF сохранён: {pdf_path}")


def build_single_pdf_from_pages(
    pages_data: list[dict],
    image_paths: list[Path | None],
    pdf_path: Path,
) -> None:
    """Собирает один PDF только из изображений страниц (текст уже на картинках — без наложения, чтобы не было прямоугольников)."""
    paths = [p for p in image_paths if p is not None and p.exists()]
    if not paths:
        print("Нет изображений для сборки PDF.")
        return
    if HAS_IMG2PDF:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in paths]))
        print(f"PDF сохранён: {pdf_path}")
    else:
        raise RuntimeError("Установите img2pdf: pip install img2pdf")


def parse_from_file(html_path: Path, out_dir: Path, make_pdf: bool = True) -> None:
    """Парсит контейнер из HTML: при наличии PDF в контейнере — скачивает его; иначе собирает один PDF из изображений и текста."""
    html = html_path.read_text(encoding="utf-8", errors="replace")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{html_path.stem}_documentation.pdf"

    pdf_url = extract_pdf_url_from_html(html)
    if pdf_url:
        print("В контейнере найден PDF, скачиваю как есть...")
        session = requests.Session()
        session.headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        try:
            r = session.get(pdf_url, timeout=60)
            r.raise_for_status()
            pdf_path.write_bytes(r.content)
            print(f"PDF сохранён: {pdf_path}")
        except Exception as e:
            print(f"Не удалось скачать PDF: {e}", file=sys.stderr)
        return

    pages_data = extract_pages_data_from_html(html)
    if not pages_data:
        print("В HTML не найдено блоков страниц (outer_page_1 …). Проверьте, что в файле есть div[role='document'].outer_page_container.")
        return
    print(f"Найдено страниц: {len(pages_data)}")
    if not make_pdf:
        return
    print("Скачиваю изображения страниц...")
    image_paths = download_page_images(pages_data, out_dir)
    build_single_pdf_from_pages(pages_data, image_paths, pdf_path)


def _scroll_container_to_bottom(page, container, pause_ms: int = 500, max_steps: int = 150) -> None:
    """Прокручивает контейнер до конца, чтобы подгрузились все lazy-loaded страницы (outer_page_1 … outer_page_97)."""
    for step in range(max_steps):
        container.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        page.wait_for_timeout(pause_ms)
        at_bottom = container.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 10"
        )
        if at_bottom:
            break


def get_html_with_playwright(url: str, timeout: int = 45000) -> str:
    """Загружает страницу через Playwright и возвращает HTML document_container (или всей страницы)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Для загрузки по URL установите: pip install playwright && playwright install chromium")

    # Реалистичный браузер, чтобы уменьшить шанс блокировки
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = context.new_page()

        try:
            # Не используем networkidle — у Scribd много фоновых запросов, часто ERR_CONNECTION_RESET
            last_err = None
            for attempt in range(1, 4):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    break
                except Exception as e:
                    last_err = e
                    if attempt < 3:
                        print(f"  Повтор {attempt}/3 через 5 сек...")
                        page.wait_for_timeout(5000)
                    else:
                        print("\nСайт Scribd может блокировать автоматический доступ или сбрасывать соединение.")
                        print("Используйте вариант с сохранённым HTML:")
                        print("  1. Откройте документ в обычном браузере, прокрутите до нужных страниц.")
                        print("  2. F12 → найдите элемент с class='document_container' → ПКМ → Copy → Copy outerHTML.")
                        print("  3. Вставьте в файл (например scribd.html) и запустите:")
                        print("     python parse_scribd_document.py --html scribd.html --out output")
                        raise RuntimeError(
                            f"Не удалось загрузить страницу после 3 попыток: {last_err}"
                        ) from last_err
            # Даём время на отрисовку. Целевой контейнер: div[role="document"].outer_page_container
            page.wait_for_timeout(5000)
            container = page.query_selector('div[role="document"].outer_page_container')
            if not container:
                container = page.query_selector('div[role="document"][class*="outer_page_container"]')
            if not container:
                container = page.query_selector("[class*='document_scroller']")
            if not container:
                container = page.query_selector(".document_container")
            if not container:
                container = page.query_selector('[role="document"]')
            if container:
                # Scribd подгружает страницы при скролле. Прокручиваем контейнер до конца, чтобы в DOM появились все outer_page_1 … outer_page_97
                print("Прокрутка документа для подгрузки всех страниц...")
                _scroll_container_to_bottom(page, container)
                page.wait_for_timeout(2000)
                html = container.inner_html()
                print("Найден контейнер документа (outer_page_container / document_scroller).")
            else:
                html = page.content()
                print("Блок .document_container не найден — извлекаем изображения со всей страницы.")
        finally:
            context.close()
            browser.close()
    return html


def parse_from_url(url: str, out_dir: Path, make_pdf: bool = True) -> None:
    """Парсит контейнер со страницы Scribd по URL: при наличии PDF — скачивает; иначе один PDF из изображений и текста."""
    print("Загрузка страницы (Playwright)...")
    html = get_html_with_playwright(url)
    wrapped = f'<div class="document_container">{html}</div>'
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "documentation.pdf"

    pdf_url = extract_pdf_url_from_html(wrapped)
    if pdf_url:
        print("В контейнере найден PDF, скачиваю как есть...")
        session = requests.Session()
        session.headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        try:
            r = session.get(pdf_url, timeout=60)
            r.raise_for_status()
            pdf_path.write_bytes(r.content)
            print(f"PDF сохранён: {pdf_path}")
        except Exception as e:
            print(f"Не удалось скачать PDF: {e}", file=sys.stderr)
        return

    pages_data = extract_pages_data_from_html(wrapped)
    if not pages_data:
        print("Страницы не найдены. Возможно, контент подгружается по скроллу — сохраните HTML контейнера вручную и используйте --html.")
        return
    print(f"Найдено страниц: {len(pages_data)}")
    if not make_pdf:
        return
    print("Скачиваю изображения страниц...")
    image_paths = download_page_images(pages_data, out_dir)
    build_single_pdf_from_pages(pages_data, image_paths, pdf_path)


def main():
    parser = argparse.ArgumentParser(description="Парсинг Scribd: один PDF (из контейнера или из изображений+текста)")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", "-u", help="URL страницы Scribd (нужен Playwright)")
    g.add_argument("--html", "-f", type=Path, help="Путь к сохранённому HTML-файлу с контейнером документа")
    parser.add_argument("--out", "-o", type=Path, default=Path("output"), help="Папка для вывода PDF (по умолчанию: output)")
    parser.add_argument("--no-pdf", action="store_true", help="Не собирать PDF (только загрузить страницу/распарсить)")
    args = parser.parse_args()

    if args.url:
        parse_from_url(args.url, args.out, make_pdf=not args.no_pdf)
    else:
        parse_from_file(args.html, args.out, make_pdf=not args.no_pdf)


if __name__ == "__main__":
    main()
