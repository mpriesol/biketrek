import argparse
import os
import re
from urllib.parse import urlparse, urljoin
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from PIL import Image
import urllib3

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


def fetch(session, url: str):
    """Načíta URL s ohľadom na TRY_SECURE_FIRST."""
    if TRY_SECURE_FIRST:
        try:
            return session.get(url, timeout=20, verify=True)
        except requests.exceptions.SSLError:
            print(f"SSL chyba pri {url}, idem bez verifikácie certifikátu...")
    # default: insecure
    return session.get(url, timeout=20, verify=False)


# ---------------------------------------
# Pomocné funkcie pre URL a filtre
# ---------------------------------------

def is_direct_image_url(url: str) -> bool:
    """Vracia True, ak je to priamy obrázok na northfinder.com/b2b.northfinder.com."""
    p = urlparse(url)
    if "northfinder.com" not in p.netloc:
        return False
    path_lower = p.path.lower()
    return any(path_lower.endswith(ext) for ext in IMAGE_EXTS)


def derive_filter_from_product_url(url: str) -> str | None:
    """
    Z URL produktu Northfinderu sa pokúsi odvodiť 'core' názov,
    ktorý je aj v názve súborov s fotkami.
    Príklad:
      /sk/8434-bu-5273sp-panska-komfortna-urban-bunda-premium-outershell-2l-phil.html
    -> bu-5273sp-panska-komfortna-urban-bunda-premium-outershell-2l-phil
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


def derive_variant_tag(url: str) -> str | None:
    """
    Z URL typu ...tayler.html/232-farba-greenblack vráti '232-farba-greenblack'.
    Ak za .html nič nie je, vráti None (PHIL typ URL).
    """
    parsed = urlparse(url)
    path = parsed.path
    if ".html/" not in path:
        return None
    after = path.split(".html/", 1)[1]
    variant = after.split("/", 1)[0]
    return variant or None


# ---------------------------------------
# Vyhľadanie obrázkov v HTML
# ---------------------------------------

def extract_northfinder_image_urls(html: str) -> list[str]:
    """
    Z HTML vytiahne všetky URL na obrázky z northfinder.com aj b2b.northfinder.com.
    Hľadá .webp/.jpg/.jpeg/.png, zachováva poradie a odstraňuje duplicity.
    """
    pattern = re.compile(
        r"https://(?:b2b\.)?northfinder\.com/[^\s\"']+?\.(?:webp|jpg|jpeg|png)(?:\?[^\s\"']*)?",
        re.IGNORECASE,
    )
    urls: list[str] = []
    seen: set[str] = set()

    for m in pattern.finditer(html):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    return urls


def filter_urls_by_substring(urls: list[str], substring: str | None) -> list[str]:
    """Ak je daný substring, nechá len URL, ktoré ho obsahujú."""
    if not substring:
        return urls
    substring = substring.lower()
    return [u for u in urls if substring in u.lower()]


def prefer_original_default(urls: list[str]) -> list[str]:
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
                         url: str,
                         out_dir: str,
                         index: int | None = None,
                         variant_tag: str | None = None):
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
        parts.append(f"{index:02d}")
    parts.append(name_no_ext)

    out_filename = "_".join(parts) + ".png"
    out_path = os.path.join(out_dir, out_filename)

    img.save(out_path, format="PNG")
    print(f"✓ Uložené: {out_path}")


def handle_direct_image_url(session, url: str, out_dir: str):
    """Ak je vstup už priamo obrázok, stiahne a skonvertuje jeden PNG."""
    os.makedirs(out_dir, exist_ok=True)
    convert_and_save_png(session, url, out_dir, index=1, variant_tag=None)


# ---------------------------------------
# Varianty (farebné verzie)
# ---------------------------------------

def find_variant_urls(html: str, base_url: str) -> list[str]:
    """
    Na produktovej stránke nájde odkazy na rovnaký produkt s inými farebnými variantmi.
    Funguje pre URL typu ...produkt.html/xxx-farba-yyy.
    Vráti absolútne URL, základná (base_url) je súčasťou zoznamu.
    """
    parsed_base = urlparse(base_url)
    base_netloc = parsed_base.netloc
    base_path = parsed_base.path

    if ".html" in base_path:
        base_html_path = base_path.split(".html", 1)[0] + ".html"
    else:
        base_html_path = base_path

    soup = BeautifulSoup(html, "html.parser")

    urls: list[str] = []
    seen: set[str] = set()

    def add_url(u: str):
        u = u.split("#", 1)[0]
        if u not in seen:
            seen.add(u)
            urls.append(u)

    add_url(base_url)

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        p = urlparse(full)
        if p.netloc != base_netloc:
            continue
        if base_html_path not in p.path:
            continue
        add_url(full)

    return urls


def process_product_page(session,
                         product_url: str,
                         filter_str: str | None,
                         out_dir: str):
    """
    Stiahne HTML produktovej stránky, vytiahne obrázky,
    prefiltuje a uloží ich ako PNG.
    """
    print(f"\nNačítavam produkt: {product_url}")
    resp = fetch(session, product_url)
    resp.raise_for_status()
    html = resp.text

    all_img_urls = extract_northfinder_image_urls(html)
    if not all_img_urls:
        print("  – Nenašli sa žiadne obrázky northfinder.com v HTML.")
        return

    filtered_urls = filter_urls_by_substring(all_img_urls, filter_str)
    if not filtered_urls:
        print(f"  – Nenašli sa obrázky zodpovedajúce filtru '{filter_str}'.")
        return

    filtered_urls = prefer_original_default(filtered_urls)

    variant_tag = derive_variant_tag(product_url)
    if variant_tag:
        print(f"  Variant: {variant_tag}, obrázkov: {len(filtered_urls)}")
    else:
        print(f"  Variant: (bez tagu), obrázkov: {len(filtered_urls)}")

    for idx, img_url in enumerate(filtered_urls, start=1):
        try:
            convert_and_save_png(session, img_url, out_dir,
                                 index=idx, variant_tag=variant_tag)
        except Exception as e:
            print(f"✗ Chyba pri {img_url}: {e}")


# ---------------------------------------
# MAIN
# ---------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stiahne produktové obrázky z Northfinderu a uloží ich ako PNG."
    )
    parser.add_argument(
        "url",
        help="URL produktu na northfinder.com alebo priamy obrázok z northfinder/b2b.northfinder.",
    )
    parser.add_argument(
        "-f",
        "--filter",
        dest="filter_str",
        help="Voliteľný filter – časť názvu produktu (napr. 'tayler', 'phil'). Ak nie je zadaný, skript sa ho pokúsi odvodiť z URL.",
    )
    parser.add_argument(
        "-o",
        "--out",
        dest="out_dir",
        help="Cieľový adresár. Ak nie je zadaný, použije sa images_<filter> alebo images.",
    )
    parser.add_argument(
        "-V",
        "--all-variants",
        dest="all_variants",
        action="store_true",
        help="Ak je zadané, pokúsi sa nájsť aj ostatné farebné varianty a stiahnuť obrázky zo všetkých.",
    )

    args = parser.parse_args()

    session = make_session()
    url = args.url.strip()

    # 1) Priama URL na obrázok (northfinder alebo b2b)
    if is_direct_image_url(url):
        out_dir = args.out_dir or "images_direct"
        print(f"Zistená priama image URL, sťahujem jeden obrázok do '{out_dir}' (PNG).")
        handle_direct_image_url(session, url, out_dir)
        return

    # 2) Produktová stránka na northfinder.com
    filter_str = args.filter_str
    if not filter_str:
        filter_str = derive_filter_from_product_url(url)
        if filter_str:
            print(f"Automaticky zvolený filter podľa URL: '{filter_str}'")
        else:
            print("Filter z URL sa nepodarilo odvodiť, použijú sa všetky nájdené obrázky.")

    if args.out_dir:
        out_dir = args.out_dir
    else:
        if filter_str:
            out_dir = f"images_{filter_str}"
        else:
            out_dir = "images"

    os.makedirs(out_dir, exist_ok=True)

    # Bez variantov – spracujeme len danú URL
    if not args.all_variants:
        process_product_page(session, url, filter_str, out_dir)
        return

    # all-variants mód – načítame HTML a hľadáme ostatné varianty
    print(f"Načítavam stránku (pre hľadanie variantov): {url}")
    resp = fetch(session, url)
    resp.raise_for_status()
    html = resp.text

    variant_urls = find_variant_urls(html, url)
    print(f"Našiel som {len(variant_urls)} variantov produktu (vrátane aktuálneho).")

    for v_url in variant_urls:
        process_product_page(session, v_url, filter_str, out_dir)


if __name__ == "__main__":
    main()
