"""Download the 8 Olist CSVs into data/raw/.

The canonical source is Kaggle (olistbr/brazilian-ecommerce), which needs an API
token. These public GitHub mirrors hold byte-identical copies, so the pipeline is
reproducible without credentials. If you have the Kaggle CLI, this is equivalent:

    kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip
"""

from pathlib import Path
from urllib.request import urlopen

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

A = "https://raw.githubusercontent.com/andrevcmelo/TCC_Andre_Melo_Pos-CESAR_SCHOOL_AED/main/"
B = "https://raw.githubusercontent.com/xor9237/brazil_ecommerce/master/"

FILES = {
    "olist_orders_dataset.csv": A,
    "olist_order_items_dataset.csv": A,
    "olist_order_payments_dataset.csv": A,
    "olist_order_reviews_dataset.csv": A,
    "olist_products_dataset.csv": A,
    "olist_customers_dataset.csv": B,
    "olist_sellers_dataset.csv": B,
    "product_category_name_translation.csv": B,
}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, base in FILES.items():
        dest = RAW / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip  {name} ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        with urlopen(base + name, timeout=120) as r:
            dest.write_bytes(r.read())
        print(f"saved {name} ({dest.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
