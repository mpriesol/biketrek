import argparse
import os
import re
from urllib.parse import urlparse, urljoin
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from PIL import Image
import urllib3
from typing import List, Optional, Set

# ---------------------------------------
# KONFIGURÁCIA SSL
# ---------------------------------------
# Ak nastavíš na True, skript sa najprv pokúsi o verify=True
# a na verify=False spadne až pri SSLError.
# DEFAULT: False => hneď "insecure" (verify=False)
TRY_SECURE_FIRST = False

IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png")


def make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; NorthfinderImageDownloader/1.0)"
    })
    # potlačíme warningy pre verify=False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def fetch(session, url):
    """Načíta URL s ohľadom na TRY_SECURE_FIRST."""
    if TRY_SECURE_FIRST:
        try:
            return session.get(url, timeout=20, verify=True)
        except requests.exceptions.SSLError:
            print("SSL chyba pri {}, idem bez verifikácie certifikátu...".format(url))
    # default: insecure
    return session.get(url, timeout=20, verify=False)


# ---------------------------------------
# Pomocné funkcie pre URL a filtre
# ---------------------------------------

def is_direct_image_url(url):
    """Vracia True, ak je to priamy obrázok na northfinder.com/b2b.northfinder.com."""
    p = urlparse(url)
    if "northfinder.com" not in p.netloc:
        return False
    path_lower = p.path.lower()
    return any(path_lower.endswith(ext) for ext in IMAGE_EXTS)


def derive_filter_from_product_url(url):
    """
    Z URL produktu Northfinderu sa pokúsi odvodiť 'core' názov,
    ktorý je aj v názve súborov s fotkami.
    """
    parsed = urlparse(url)
    segments = [seg for seg in parsed.path.split("/") if seg]

    for seg in segments:
        if ".html" in seg:
            base = seg.split(".html", 1)[0]
            parts = base.split("-", 1)
            if len(parts) == 2 and parts[0].isdigit():
                base = parts[1]  # zahodíme číselný prefix
            return base.lower()
    return None


def derive_variant_tag(url):
    """
    Z URL typu ...tayler.html/232-farba-greenblack vráti '232-farba-greenblack'.
    Ak za .html nič nie je, vráti None (PHIL typ URL).
    """
    parsed = urlparse(url)
    path = parsed.path
    if ".html/" not in path:
        return None
    after = path.split(".html", 1)[1]
    after = after.lstrip("/")
    variant = after.split("/", 1)[0]
    return variant or None


# ---------------------------------------
# Vyhľadanie obrázkov v HTML
# ---------------------------------------

def extract_northfinder_image_urls(html):
    """
    Z HTML vytiahne všetky URL na obrázky z northfinder.com aj b2b.northfinder.com.
    Hľadá .webp/.jpg/.jpeg/.png, zachováva poradie a odstraňuje duplicity.
    """
    pattern = re.compile(
        r"https://(?:b2b\.)?northfinder\.com/[^\s\"']+?\.(?:webp|jpg|jpeg|png)(?:\?[^\s\"']*)?",
        re.IGNORECASE,
    )
    urls = []  # type: List[str]
    seen = set()  # type: Set[str]

    for m in pattern.finditer(html):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    return urls


def filter_urls_by_substring(urls, substring):
    """Ak je daný substring, nechá len URL, ktoré ho obsahujú."""
    if not substring:
        return urls
    substring = substring.lower()
    return [u for u in urls if substring in u.lower()]


def prefer_b2b(urls):
    """
    Ak sú k dispozícii b2b.northfinder.com obrázky, použije iba tie.
    Inak vráti pôvodný zoznam.
    """
    b2b = [u for u in urls if "b2b.northfinder.com" in u]
    return b2b if b2b else urls


def prefer_original_default(urls):
    """
    Ak sú k dispozícii original_default verzie, použije iba tie.
    Inak vráti pôvodný zoznam.
    """
    originals = [u for u in urls if "original_default" in u]
    return originals if originals else urls


# ---------------------------------------
# Ukladanie obrázkov
# ---------------------------------------

def convert_and_save_png(session,
                         url,
                         out_dir,
                         index=None,
                         variant_tag=None):
    """
    Stiahne obrázok, skonvertuje do PNG a uloží s unikátnym názvom.
    """
    download_url = url
    base_url = url.split("?", 1)[0]

    resp = fetch(session, download_url)
    resp.raise_for_status()

    img = Image.open(BytesIO(resp.content)).convert("RGBA")

    filename = os.path.basename(urlparse(base_url).path)
    name_no_ext, _ = os.path.splitext(filename)

    parts = []
    if variant_tag:
        parts.append(variant_tag)
    if index is not None:
        parts.append("{:02d}".format(index))
    parts.append(name_no_ext)

    out_filename = "_".join(parts) + ".png"
    out_path = os.path.join(out_dir, out_filename)

    img.save(out_path, format="PNG")
    print("✓ Uložené: {}".format(out_path))


def handle_direct_image_url(session, url, out_dir):
    """Ak je vstup už priamo obrázok, stiahne a skonvertuje jeden PNG."""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    convert_and_save_png(session, url, out_dir, index=1, variant_tag=None)


# ---------------------------------------
# Varianty (farebné verzie)
# ---------------------------------------

def find_variant_urls(html, base_url_no_query):
    """
    Na produktovej stránke nájde odkazy na rovnaký produkt s inými farebnými variantmi.
    Funguje pre URL typu ...produkt.html/xxx-farba-yyy.
    Vráti absolútne URL, základná (base_url_no_query) je súčasťou zoznamu.
    """
    parsed_base = urlparse(base_url_no_query)
    base_netloc = parsed_base.netloc
    base_path = parsed_base.path

    if ".html" in base_path:
        base_html_path = base_path.split(".html", 1)[0] + ".html"
    else:
        base_html_path = base_path

    soup = BeautifulSoup(html, "html.parser")

    urls = []  # type: List[str]
    seen = set()  # type: Set[str]

    def add_url(u):
        u = u.split("#", 1)[0]
        if u not in seen:
            seen.add(u)
            urls.append(u)

    add_url(base_url_no_query)

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url_no_query, a["href"])
        p = urlparse(full)
        if p.netloc != base_netloc:
            continue
        if base_html_path not in p.path:
            continue
        add_url(full)

    return urls


def process_product_page(session,
                         product_url,
                         filter_str,
                         out_dir):
    """
    Stiahne HTML produktovej stránky, vytiahne obrázky,
    prefiltuje a uloží ich ako PNG.
    """
    print("\nNačítavam produkt: {}".format(product_url))
    resp = fetch(session, product_url)
    resp.raise_for_status()
    html = resp.text

    all_img_urls = extract_northfinder_image_urls(html)
    if not all_img_urls:
        print("  – Nenašli sa žiadne obrázky northfinder.com v HTML.")
        return

    filtered_urls = filter_urls_by_substring(all_img_urls, filter_str)
    if not filtered_urls:
        print("  – Nenašli sa obrázky zodpovedajúce filtru '{}'.".format(filter_str))
        return

    # najprv preferuj b2b, až potom original_default
    filtered_urls = prefer_b2b(filtered_urls)
    filtered_urls = prefer_original_default(filtered_urls)

    variant_tag = derive_variant_tag(product_url)
    if variant_tag:
        print("  Variant: {}, obrázkov: {}".format(variant_tag, len(filtered_urls)))
    else:
        print("  Variant: (bez tagu), obrázkov: {}".format(len(filtered_urls)))

    for idx, img_url in enumerate(filtered_urls, start=1):
        try:
            convert_and_save_png(session, img_url, out_dir,
                                 index=idx, variant_tag=variant_tag)
        except Exception as e:
            print("✗ Chyba pri {}: {}".format(img_url, e))


# ---------------------------------------
# MAIN
# ---------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stiahne produktové obrázky z Northfinderu a uloží ich ako PNG."
    )
    parser.add_argument(
        "url",
        help="URL produktu na northfinder.com (aj s ?search_query=...) alebo priamy "
             "obrázok z northfinder/b2b.northfinder.",
    )
    parser.add_argument(
        "-f",
        "--filter",
        dest="filter_str",
        help="Voliteľný filter – časť názvu produktu (napr. 'tayler', 'phil'). "
             "Ak nie je zadaný, skript sa ho pokúsi odvodiť z URL (bez query).",
    )
    parser.add_argument(
        "-o",
        "--out",
        dest="out_dir",
        help="Cieľový priečinok. Ak nie je zadaný, použije sa images_<filter> alebo images.",
    )
    parser.add_argument(
        "-V",
        "--all-variants",
        dest="all_variants",
        action="store_true",
        help="Ak je zadané, pokúsi sa nájsť aj ostatné farebné varianty a stiahnuť "
             "obrázky zo všetkých.",
    )

    args = parser.parse_args()

    raw_url = args.url.strip()
    url_no_query = raw_url.split("?", 1)[0]

    session = make_session()

    # 1) Priama URL na obrázok (northfinder alebo b2b)
    if is_direct_image_url(raw_url):
        out_dir = args.out_dir or "images_direct"
        print("Zistená priama image URL, sťahujem jeden obrázok do '{}' (PNG).".format(out_dir))
        handle_direct_image_url(session, raw_url, out_dir)
        return

    # 2) Produktová stránka na northfinder.com (aj s ?search_query)
    filter_str = args.filter_str
    if not filter_str:
        filter_str = derive_filter_from_product_url(url_no_query)
        if filter_str:
            print("Automaticky zvolený filter podľa URL: '{}'".format(filter_str))
        else:
            print("Filter z URL sa nepodarilo odvodiť, použijú sa všetky nájdené obrázky.")

    if args.out_dir:
        out_dir = args.out_dir
    else:
        if filter_str:
            out_dir = "images_{}".format(filter_str)
        else:
            out_dir = "images"

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    # Bez variantov – spracujeme len danú URL (vrátane query)
    if not args.all_variants:
        process_product_page(session, raw_url, filter_str, out_dir)
        return

    # all-variants mód – načítame HTML a hľadáme ostatné varianty (bez query)
    print("Načítavam stránku (pre hľadanie variantov): {}".format(raw_url))
    resp = fetch(session, raw_url)
    resp.raise_for_status()
    html = resp.text

    variant_urls = find_variant_urls(html, url_no_query)
    print("Našiel som {} variantov produktu (vrátane aktuálneho).".format(len(variant_urls)))

    for v_url in variant_urls:
        process_product_page(session, v_url, filter_str, out_dir)


if __name__ == "__main__":
    main()
