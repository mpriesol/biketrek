# make_upgates_variants_v3 — README

Skript na zlúčenie viacerých podobných produktov z Upgates exportu do **jedného hlavného produktu** s **variantmi**.

---

## Čo skript robí

- Zo vstupného CSV/XLSX vytvorí: **1× MAIN + N× VARIANT**.
- **[PRODUCT_CODE]**: všetky riadky (MAIN + varianty) dostanú **rovnaký rodičovský kód** (z `--product-code`).
- **[VARIANT_CODE]**: každý variant dostane **pôvodný [PRODUCT_CODE]** zo svojho vstupného riadku.
- **Texty/SEO/kategórie/výrobca…** ostávajú **len na MAIN**; vo variantoch sa **vyprázdnia**.
- **Parametre**: zvolený rozlišovací (napr. „Farba“, „Balenie“) zostáva **na variantoch** (na MAIN sa vyprázdni).  
  Parametre s **rovnakou hodnotou vo všetkých riadkoch** sa presunú **len na MAIN** (vo variantoch prázdne).
- **EAN/Stock/Weight/Price*** ostávajú **na variantoch**; na **MAIN** sú `EAN/STOCK/WEIGHT` prázdne, `IS_PRICES_WITH_VAT_YN=1`.
- **Obrázky**: **MAIN =** zjednotený zoznam všetkých unikátnych; **VARIANT =** presne **prvý** z daného riadku.
- **Štítky**: všetky `LABEL_ACTIVE_YN „…“` = **0**.

> Vstup má obsahovať **iba riadky**, ktoré patria pod jedného rodiča. Skript nerobí automatické zoskupovanie.

---

## Inštalácia

Vyžaduje **Python 3.9+** a **pandas**:

```bash
pip install pandas
```

---

## Rýchly štart

```bash
python make_upgates_variants_v3.py \
  -i "vstup.csv" \
  -o "vystup.csv" \
  --param "[PARAMETER „Farba“]" \
  --product-code SLG-11451 \
  --title "FORCE zvonček KLASIK Fe / plast 22,2mm" \
  --excel-bom
```

- `--excel-bom` = zapisuje **UTF-8 s BOM** (Excel pri dvojkliku korektne zobrazí diakritiku).
- Ak chceš **čistý UTF-8**, použi `--out-encoding utf-8` a importuj v Exceli cez **Data → From Text/CSV → File Origin = UTF-8**.

---

## Parametre

| Parameter | Povinné | Default | Popis |
|---|---:|---|---|
| `-i`, `--input` | ✔ | – | Vstupný CSV/XLSX s produktmi, ktoré sa majú zlúčiť. |
| `-o`, `--output` |  | `<input>_variants.<ext>` | Výstupný CSV. |
| `--param` |  | interaktívny výber | Hlavička rozlišovacieho parametra (napr. `[PARAMETER „Farba“]`, `[PARAMETER „Balenie“]`). |
| `--product-code` |  | z 1. riadku | Rodičovský `[PRODUCT_CODE]` pre **všetky** riadky vo výstupe. |
| `--title` |  | z 1. riadku | `TITLE` hlavného produktu. |
| `--template-index` |  | `0` | Index (0-based) riadku, z ktorého sa berie „šablóna“ pre MAIN (TITLE/SEO/VAT/CATEGORIES…). |
| `--out-encoding` |  | `utf-8` | Kódovanie výstupu (napr. `utf-8`, `utf-8-sig`). |
| `--excel-bom` |  | `False` | Alias pre `--out-encoding utf-8-sig` (odporúčané pre Excel dvojklik). |

---

## Poznámky

- Vstupné CSV sa načítava s autodetekciou oddeľovača (`;`, `,`, `\t`, `|`) a kódovania (`utf-8-sig`, `utf-8`, `cp1250`, `iso-8859-2`, `latin1`) – skript vyberie variant s **najmenej** náhradnými znakmi `�`.
- Poradie stĺpcov zachováva pôvodný export.
- Ak potrebuješ zároveň uložiť pôvodný `[PRODUCT_CODE]` aj do meta stĺpca (napr. `[META „original_product_code“]`), dá sa dorobiť prepínač.
