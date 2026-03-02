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


def extract_images_from_html(html: str) -> list[str]:
    """
    Извлекает URL изображений из HTML блока document_container.
    Учитывает: <img src="..."> и background-image в style.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(class_="document_container")
    if not container:
        container = soup  # весь документ

    urls = []

    # 1) Все img
    for img in container.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if src and not src.startswith("data:"):
            urls.append(src)

    # 2) background-image в style
    bg_re = re.compile(r"background-image:\s*url\(['\"]?([^'\")\s]+)['\"]?\)")
    for tag in container.find_all(style=True):
        style = tag.get("style", "")
        for m in bg_re.finditer(style):
            u = m.group(1).strip()
            if u and not u.startswith("data:"):
                urls.append(u)

    # убираем дубликаты, сохраняя порядок
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


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


def get_html_with_playwright(url: str, timeout: int = 60000) -> str:
    """Загружает страницу через Playwright и возвращает HTML document_container (или всей страницы)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Для загрузки по URL установите: pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)
        page.wait_for_selector(".document_container", timeout=timeout)
        container = page.query_selector(".document_container")
        html = container.inner_html() if container else page.content()
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
