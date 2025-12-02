from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from apify import Actor


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_html_sync(url: str, proxy_url: str | None) -> str:
    """Senkron HTTP GET, optional Apify proxy ile."""
    proxies = None
    if proxy_url:
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=30)
    resp.raise_for_status()
    return resp.text


import re

LYRICS_URL_RE = re.compile(r"^https?://genius\.com/.+-lyrics/?$")


def parse_search_results(html: str, max_songs: int):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # 1) Eski layout için önce mini_card denemesi
    for a in soup.select("a.mini_card"):
        if len(results) >= max_songs:
            break

        url = a.get("href") or ""
        if not LYRICS_URL_RE.match(url):
            continue

        title_el = a.select_one(".mini_card-title")
        artist_el = a.select_one(".mini_card-subtitle")

        song_title = title_el.get_text(strip=True) if title_el else ""
        artist_name = artist_el.get_text(strip=True) if artist_el else ""

        if not song_title:
            # Başka bir text kaynağı dene
            text = a.get_text(" ", strip=True)
            if " – " in text:
                song_title, artist_name = text.split(" – ", 1)
            elif " - " in text:
                song_title, artist_name = text.split(" - ", 1)
            else:
                song_title = text

        if not song_title:
            continue

        results.append(
            {
                "url": url,
                "song_title": song_title.strip(),
                "artist_name": artist_name.strip(),
            }
        )

    # 2) mini_card hiç yoksa: tüm <a> tag'lerinden lyrics linklerini ara
    if not results:
        for a in soup.find_all("a", href=True):
            if len(results) >= max_songs:
                break

            url = a["href"]
            if not LYRICS_URL_RE.match(url):
                continue

            text = a.get_text(" ", strip=True)
            if not text:
                continue

            song_title = text
            artist_name = ""

            if " – " in text:
                song_title, artist_name = text.split(" – ", 1)
            elif " - " in text:
                song_title, artist_name = text.split(" - ", 1)

            results.append(
                {
                    "url": url,
                    "song_title": song_title.strip(),
                    "artist_name": artist_name.strip(),
                }
            )

    return results



def parse_lyrics(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Yeni layout
    blocks = [
        div.get_text(separator="\n", strip=True)
        for div in soup.select('div[data-lyrics-container="true"]')
    ]
    text = "\n\n".join([b for b in blocks if b])

    # Eski layout fallback
    if not text:
        old = soup.select_one(".lyrics")
        if old:
            text = old.get_text(separator="\n", strip=True)

    return (text or "").strip()


async def main() -> None:
    """Entry point for the Genius lyrics scraper Actor."""
    async with Actor:
        actor_input = await Actor.get_input() or {}
        search_query = actor_input.get("searchQuery")
        max_songs = int(actor_input.get("maxSongs", 1) or 1)

        if not search_query:
            raise RuntimeError("searchQuery is required")

        search_url = f"https://genius.com/search?q={quote_plus(search_query)}"
        Actor.log.info("Search URL: %s", search_url)

        # Apify Proxy konfigürasyonu (free krediden kullanır)
        try:
            proxy_cfg = await Actor.create_proxy_configuration()
            proxy_url = await proxy_cfg.new_url() if proxy_cfg else None
        except Exception as e:
            Actor.log.warning("Failed to create proxy configuration: %s", e)
            proxy_url = None

        if proxy_url:
            Actor.log.info("Using Apify proxy: %s", proxy_url)
        else:
            Actor.log.warning("No proxy configuration available, using direct connection.")

        loop = asyncio.get_running_loop()

        # 1) Arama sayfası – burada 403 alırsak kırılma yerine boş sonuç yazıp çıkıyoruz
        try:
            search_html = await loop.run_in_executor(
                None, fetch_html_sync, search_url, proxy_url
            )
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            Actor.log.warning("Failed to fetch search page: %s (status=%s)", e, status)

            # 403 veya başka hata → yine de bir kayıt push ediyoruz ki consumer bunu görsün
            await Actor.push_data(
                {
                    "searchQuery": search_query,
                    "songTitle": None,
                    "artistName": None,
                    "url": search_url,
                    "lyricsText": "",
                    "error": f"Failed to fetch search page (status={status})",
                }
            )
            return

        candidates = parse_search_results(search_html, max_songs)
        Actor.log.info("Found %d candidates", len(candidates))

        if not candidates:
            await Actor.push_data(
                {
                    "searchQuery": search_query,
                    "songTitle": None,
                    "artistName": None,
                    "url": search_url,
                    "lyricsText": "",
                    "error": "No candidates found on Genius search page",
                }
            )
            return

        # 2) Her şarkı sayfası
        for c in candidates:
            Actor.log.info("Fetching song page: %s", c["url"])

            try:
                song_html = await loop.run_in_executor(
                    None, fetch_html_sync, c["url"], proxy_url
                )
                lyrics_text = parse_lyrics(song_html)
                error_msg = None
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                Actor.log.warning("Failed to fetch song page: %s (status=%s)", e, status)
                lyrics_text = ""
                error_msg = f"Failed to fetch song page (status={status})"

            await Actor.push_data(
                {
                    "searchQuery": search_query,
                    "songTitle": c["song_title"],
                    "artistName": c["artist_name"],
                    "url": c["url"],
                    "lyricsText": lyrics_text,
                    "error": error_msg,
                }
            )
            Actor.log.info(
                "Pushed lyrics for %s - %s (len=%d)",
                c["artist_name"],
                c["song_title"],
                len(lyrics_text),
            )