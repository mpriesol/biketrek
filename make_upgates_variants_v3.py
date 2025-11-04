#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_upgates_variants_v3.py

Build a MAIN product + VARIANTS for Upgates from a CSV/Excel export.

Key behavior (per user spec):
- All rows (MAIN + variants) share the same [PRODUCT_CODE] (parent code you provide or taken from the first row).
- Each variant’s [VARIANT_CODE] = its ORIGINAL per-row [PRODUCT_CODE] from the input (so the old codes are preserved).
- MAIN has [VARIANT_CODE] empty.
- Text/SEO/CATEGORIES/MANUFACTURER/… only on MAIN; variants have them blank.
- PARAMETER columns: the selected distinguishing one stays on variants (blank on MAIN); parameters with same value across all rows move to MAIN and are blank on variants.
- Per-variant EAN/STOCK/WEIGHT/PRICE_* stay pri variantoch; MAIN má EAN/STOCK/WEIGHT prázdne. IS_PRICES_WITH_VAT_YN = 1 všade.
- IMAGES: MAIN = union unikátnych; VARIANT = presne prvý obrázok zo svojho riadku.
- LABEL_ACTIVE_YN „…“ = 0 všade.

I/O:
- CSV/Excel input, autodetekcia oddeľovača a (csv) kódovania (utf-8-sig, utf-8, cp1250, iso-8859-2, latin1).
- Výstup default UTF-8 (bez BOM). Pre Excel dvojklik použi --excel-bom (zapíše utf-8-sig).

Examples:
  python make_upgates_variants_v3.py -i input.csv -o output.csv --param "[PARAMETER „Balenie“]" --product-code 22846316
  python make_upgates_variants_v3.py -i input.csv -o output.csv --excel-bom
"""
import argparse, csv, io, re
from pathlib import Path
from typing import List
import pandas as pd

POSSIBLE_ENCODINGS = ("utf-8-sig","utf-8","cp1250","iso-8859-2","latin1")

def sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",",";","\t","|"])
        return dialect.delimiter
    except Exception:
        return ";" if sample.count(";") >= sample.count(",") else ","

def read_csv_safely(p: Path):
    raw = p.read_bytes()
    best = None  # (df, enc, delim, penalty, priority)
    priority = {"utf-8-sig":0,"utf-8":1,"cp1250":2,"iso-8859-2":3,"latin1":4}
    for enc in POSSIBLE_ENCODINGS:
        try:
            text = raw.decode(enc)
        except Exception:
            continue
        pen = text.count(" ")
        delim = sniff_delimiter(text[:4096])
        try:
            df = pd.read_csv(io.StringIO(text), delimiter=delim, dtype=str).fillna("")
        except Exception:
            continue
        cand = (df, enc, delim, pen, priority.get(enc, 9))
        if best is None or (pen, cand[4]) < (best[3], best[4]):
            best = cand
    if best is None:
        text = raw.decode("utf-8", errors="replace")
        delim = sniff_delimiter(text[:4096])
        df = pd.read_csv(io.StringIO(text), delimiter=delim, dtype=str).fillna("")
        return df, "utf-8", delim
    return best[0], best[1], best[2]

def read_any_table(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p, dtype=str).fillna("")
        return df, "excel", "excel"
    return read_csv_safely(p)

def normalize_header_map(cols):
    m = {}
    def find(name_variants):
        for v in name_variants:
            for c in cols:
                if c.strip().lower() == v.lower():
                    return c
        for v in name_variants:
            for c in cols:
                if v.lower() in c.strip().lower():
                    return c
        return None
    base_fields = [
        "PRODUCT_CODE","VARIANT_YN","VARIANT_CODE","MAIN_YN","ACTIVE_YN",
        "ARCHIVED_YN","CAN_ADD_TO_BASKET_YN","LANGUAGE","TITLE","LONG_DESCRIPTION",
        "SHORT_DESCRIPTION","SEO_URL","SEO_TITLE","SEO_DESCRIPTION","EAN",
        "MANUFACTURER","AVAILABILITY","AVAILABILITY_NOTE","STOCK","WEIGHT","UNIT",
        "SHIPMENT_GROUP","VAT","CATEGORIES","IMAGES","FILES","IS_PRICES_WITH_VAT_YN"
    ]
    for f in base_fields:
        m[f] = find([f"[{f}]", f])
    m["PRICE_COLS"]  = [c for c in cols if "price" in c.lower()]
    m["LABEL_COLS"]  = [c for c in cols if "LABEL_ACTIVE_YN" in c]
    m["PARAM_COLS"]  = [c for c in cols if "PARAMETER" in c.upper()]
    return m

def ensure_col(df, col: str):
    if col and col not in df.columns:
        df[col] = ""

def merge_images_unique(values: List[str]) -> str:
    seen, out = set(), []
    for v in values:
        if not v: continue
        for p in re.split(r"[;|,]\s*", str(v).strip()):
            p = p.strip()
            if p and p not in seen:
                seen.add(p); out.append(p)
    return ";".join(out)

def first_image(value: str) -> str:
    if not value: return ""
    parts = re.split(r"[;|,]\s*", str(value).strip())
    return parts[0].strip() if parts else ""

def build_variants(df, param_col: str, parent_code: str, main_title: str = None, template_index: int = 0):
    cols = list(df.columns)
    h = normalize_header_map(cols)
    if not h["PRODUCT_CODE"]:
        raise RuntimeError("Missing [PRODUCT_CODE] header.")
    # fuzzy param match
    if param_col and param_col not in h["PARAM_COLS"]:
        matches = [c for c in h["PARAM_COLS"] if param_col.strip().lower() in c.lower()]
        if matches: param_col = matches[0]
    # common PARAMETERs (same across all) → move to MAIN only
    common_param_cols = []
    for pc in h["PARAM_COLS"]:
        vals = [str(v).strip() for v in df[pc].tolist() if str(v).strip() != ""]
        if len(set(vals)) <= 1:
            common_param_cols.append(pc)

    base = df.iloc[template_index].copy()
    base[h["PRODUCT_CODE"]] = parent_code
    main_only = [h.get(k) for k in ("TITLE","LONG_DESCRIPTION","SHORT_DESCRIPTION","SEO_URL","SEO_TITLE","SEO_DESCRIPTION",
                                    "MANUFACTURER","AVAILABILITY","AVAILABILITY_NOTE","UNIT","VAT","CATEGORIES")]
    main_only = [c for c in main_only if c]

    # Ensure presence of flags/price/labels
    for k in ("VARIANT_YN","VARIANT_CODE","MAIN_YN","ACTIVE_YN","ARCHIVED_YN","CAN_ADD_TO_BASKET_YN","IS_PRICES_WITH_VAT_YN"):
        ensure_col(df, h.get(k) or f"[{k}]")
    for c in h["LABEL_COLS"]: ensure_col(df, c)
    for c in h["PRICE_COLS"]: ensure_col(df, c)

    # MAIN flags
    if h["LANGUAGE"]: base[h["LANGUAGE"]] = "sk"
    if h["VARIANT_YN"]: base[h["VARIANT_YN"]] = "0"
    if h["VARIANT_CODE"]: base[h["VARIANT_CODE"]] = ""
    if h["MAIN_YN"]: base[h["MAIN_YN"]] = ""
    if h["ACTIVE_YN"]: base[h["ACTIVE_YN"]] = "1"
    if h["ARCHIVED_YN"]: base[h["ARCHIVED_YN"]] = "0"
    if h["CAN_ADD_TO_BASKET_YN"]: base[h["CAN_ADD_TO_BASKET_YN"]] = "1"
    if h["IS_PRICES_WITH_VAT_YN"]: base[h["IS_PRICES_WITH_VAT_YN"]] = "1"
    if h["EAN"]: base[h["EAN"]] = ""
    if h["STOCK"]: base[h["STOCK"]] = ""
    if h["WEIGHT"]: base[h["WEIGHT"]] = ""
    if param_col: base[param_col] = ""
    if main_title and h["TITLE"]: base[h["TITLE"]] = main_title
    if h["IMAGES"]: base[h["IMAGES"]] = merge_images_unique(df[h["IMAGES"]].tolist())
    for c in h["LABEL_COLS"]: base[c] = "0"

    # VARIANTS
    variants = []
    for i, row in df.reset_index(drop=True).iterrows():
        vr = row.copy()
        # capture original code FIRST
        orig_code = row[h["PRODUCT_CODE"]]
        vr[h["PRODUCT_CODE"]] = parent_code
        if h["VARIANT_YN"]: vr[h["VARIANT_YN"]] = "1"
        if h["VARIANT_CODE"]: vr[h["VARIANT_CODE"]] = orig_code  # <-- change requested
        if h["MAIN_YN"]: vr[h["MAIN_YN"]] = "0"
        if h["ACTIVE_YN"]: vr[h["ACTIVE_YN"]] = "1"
        if h["ARCHIVED_YN"]: vr[h["ARCHIVED_YN"]] = ""
        if h["CAN_ADD_TO_BASKET_YN"]: vr[h["CAN_ADD_TO_BASKET_YN"]] = ""
        if h["IS_PRICES_WITH_VAT_YN"]: vr[h["IS_PRICES_WITH_VAT_YN"]] = "1"
        # clear main-only fields
        for c in main_only: vr[c] = ""
        # parameters: those that are common move off variants; keep distinguishing one
        for pc in common_param_cols: vr[pc] = ""
        # enforce exactly one image per variant
        if h["IMAGES"]: vr[h["IMAGES"]] = first_image(row[h["IMAGES"]])
        # labels to 0
        for c in h["LABEL_COLS"]: vr[c] = "0"
        variants.append(vr)

    out_rows = [base] + variants
    out = pd.DataFrame(out_rows, columns=cols)
    return out

def ask(prompt: str, default: str = "") -> str:
    s = input(f"{prompt} [{default}]: ").strip()
    return s or default

def main():
    ap = argparse.ArgumentParser(description=("Create a parent product with variants for Upgates CSV/Excel.\n"
                                              "Default write encoding UTF-8 (no BOM); use --excel-bom for Excel."))
    ap.add_argument("-i","--input", required=True, help="Input CSV/Excel with ONLY the products to merge into variants.")
    ap.add_argument("-o","--output", help="Output CSV path. Default: <input>_variants.<ext>")
    ap.add_argument("--param", help="Header of the distinguishing variant parameter, e.g. '[PARAMETER „Balenie“]'.")
    ap.add_argument("--product-code", help="Shared parent [PRODUCT_CODE]. Default = first row's [PRODUCT_CODE].")
    ap.add_argument("--title", help="Main product TITLE. Default = first row's TITLE.")
    ap.add_argument("--template-index", type=int, default=0, help="Which row (0-based) to use as template for MAIN.")
    ap.add_argument("--out-encoding", default="utf-8", help="Output encoding (default utf-8).")
    ap.add_argument("--excel-bom", action="store_true", help="Write UTF-8 with BOM (alias for --out-encoding utf-8-sig).")
    args = ap.parse_args()

    df, enc, delim = read_any_table(args.input)
    headers = normalize_header_map(list(df.columns))

    parent_code = args.product_code or (df[headers["PRODUCT_CODE"]].iloc[0] if headers["PRODUCT_CODE"] else "")
    if not parent_code:
        parent_code = ask("Enter [PRODUCT_CODE] to use for all variants", "NEWCODE")

    param_col = args.param
    if not param_col:
        pc = headers["PARAM_COLS"]
        if len(pc) == 1:
            param_col = pc[0]
            print(f"Using only PARAMETER column found: {param_col}")
        elif len(pc) > 1:
            print("PARAMETER columns detected:")
            for idx, c in enumerate(pc, 1):
                print(f"  {idx}. {c}")
            pick = ask("Pick column number to use as the distinguishing variant parameter", "1")
            try: param_col = pc[int(pick)-1]
            except Exception: param_col = pc[0]
        else:
            param_col = ""

    main_title = args.title or (df[headers["TITLE"]].iloc[0] if headers["TITLE"] else "")
    if not args.title:
        t = ask("Title for MAIN product (leave empty to keep first row's TITLE)", main_title)
        if t: main_title = t

    out = build_variants(df, param_col=param_col, parent_code=parent_code, main_title=main_title, template_index=args.template_index)

    base = Path(args.input)
    out_path = args.output or str(base.with_name(base.stem + "_variants" + base.suffix))

    out_enc = "utf-8-sig" if args.excel_bom else args.out_encoding
    if isinstance(delim, str) and delim in (",",";","\t","|"):
        out.to_csv(out_path, index=False, sep=delim, encoding=out_enc)
    else:
        out.to_csv(out_path, index=False, encoding=out_enc)

    print(f"Detected input encoding: {enc}; delimiter: {delim}")
    print(f"Wrote {out.shape[0]} rows to {out_path} with encoding: {out_enc}")

if __name__ == "__main__":
    main()
