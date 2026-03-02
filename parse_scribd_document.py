#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер изображений из Scribd document_container.
Извлекает скрины/страницы документации и сохраняет в папку и (опционально) в один PDF.
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


def extract_images_from_html(html: str) -> list[str]:
    """
    Парсит внутри <div role="document" class="outer_page_container"> все блоки
    <div class="outer_page only_ie6_border" id="outer_page_1"> ... id="outer_page_97"> —
    по порядку id (outer_page_1, outer_page_2, ..., outer_page_97), из каждого одно изображение.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one('div[role="document"].outer_page_container') or soup.select_one(
        'div[role="document"][class*="outer_page_container"]'
    )
    if not container:
        container = soup.select_one(".outer_page_container") or soup.find(attrs={"role": "document"}) or soup

    # Блоки с id="outer_page_1" … "outer_page_97" (и классы outer_page, only_ie6_border)
    id_re = re.compile(r"^outer_page_(\d+)$")
    page_divs = container.find_all(id=id_re)
    if not page_divs:
        page_divs = container.select(".outer_page.only_ie6_border")
    if not page_divs:
        page_divs = container.find_all(class_=lambda c: c and "outer_page" in (c if isinstance(c, list) else [c]) and "only_ie6_border" in (c if isinstance(c, list) else [c]))

    # Сортируем по номеру в id (outer_page_1 → 1, outer_page_97 → 97); без id — по порядку в разметке
    def sort_key(div):
        mid = id_re.match(div.get("id") or "")
        return (int(mid.group(1)), 0) if mid else (9999, id(div))

    page_divs = sorted(page_divs, key=sort_key)

    urls = []
    for div in page_divs:
        url = _image_url_from_node(div)
        if url:
            urls.append(url)
    return urls


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


def images_to_pdf(image_paths: list[Path], pdf_path: Path) -> None:
    """Собирает список изображений в один PDF."""
    if not HAS_IMG2PDF:
        raise RuntimeError("Установите img2pdf: pip install img2pdf")
    with open(pdf_path, "wb") as f:
        f.write(img2pdf.convert([str(p) for p in image_paths]))
    print(f"PDF сохранён: {pdf_path}")


def parse_from_file(html_path: Path, out_dir: Path, make_pdf: bool = True) -> None:
    """Парсит document_container из сохранённого HTML-файла."""
    html = html_path.read_text(encoding="utf-8", errors="replace")
    urls = extract_images_from_html(html)
    if not urls:
        print("В HTML не найдено изображений в document_container. Проверьте, что в файле есть блок с class='document_container' и теги img или background-image.")
        return
    print(f"Найдено URL изображений: {len(urls)}")
    paths = download_images(urls, out_dir)
    if make_pdf and paths and HAS_IMG2PDF:
        pdf_path = out_dir / f"{html_path.stem}_documentation.pdf"
        images_to_pdf(paths, pdf_path)


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
    """Парсит document_container со страницы Scribd по URL (через Playwright)."""
    print("Загрузка страницы (Playwright)...")
    html = get_html_with_playwright(url)
    # оборачиваем в div с классом для единого парсера
    wrapped = f'<div class="document_container">{html}</div>'
    urls = extract_images_from_html(wrapped)
    if not urls:
        print("Изображения не найдены. Возможно, контент подгружается по скроллу — сохраните HTML страницы вручную и используйте --html.")
        return
    print(f"Найдено URL изображений: {len(urls)}")
    paths = download_images(urls, out_dir)
    if make_pdf and paths and HAS_IMG2PDF:
        pdf_path = out_dir / "documentation.pdf"
        images_to_pdf(paths, pdf_path)


def main():
    parser = argparse.ArgumentParser(description="Парсинг изображений из Scribd document_container")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", "-u", help="URL страницы Scribd (нужен Playwright)")
    g.add_argument("--html", "-f", type=Path, help="Путь к сохранённому HTML-файлу с document_container")
    parser.add_argument("--out", "-o", type=Path, default=Path("output"), help="Папка для изображений и PDF (по умолчанию: output)")
    parser.add_argument("--no-pdf", action="store_true", help="Не собирать PDF, только сохранить изображения")
    args = parser.parse_args()

    if args.url:
        parse_from_url(args.url, args.out, make_pdf=not args.no_pdf)
    else:
        parse_from_file(args.html, args.out, make_pdf=not args.no_pdf)


if __name__ == "__main__":
    main()
