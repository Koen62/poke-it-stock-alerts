import json
import os
import smtplib
import ssl
import sys
from decimal import Decimal, DecimalException
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen

SOURCES = {
    "Pokémon South Africa": ("shopify", "https://www.pokemonsouthafrica.store/collections/pokemon-tcg-products-english"),
    "Level Up Store": ("shopify_collections", "https://levelupstore.co.za"),
    "The Geek Home": ("woocommerce_category", "https://www.geekhome.co.za"),
    "Weebforce": ("shopify", "https://www.weebforce.co.za/collections/pokemon"),
    "Rocket Grunt TCG": ("woocommerce", "https://rocketgrunttcg.co.za"),
    "Viridian Archives": ("woocommerce", "https://viridianarchives.co.za"),
}
STATE = Path("stock-state.json")


def send(subject, body):
    message = EmailMessage()
    message["From"] = os.environ["ALERT_SENDER"]
    message["To"] = os.environ["ALERT_RECIPIENT"]
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30, context=ssl.create_default_context()) as smtp:
        smtp.login(os.environ["ALERT_SENDER"], os.environ["ALERT_APP_PASSWORD"])
        smtp.send_message(message)


def scan_shopify(collection, allow_empty=False):
    variants = {}
    for page in range(1, 51):
        request = Request(f"{collection}/products.json?limit=250&page={page}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=12) as response:
            products = json.load(response)["products"]
        for product in products:
            for variant in product["variants"]:
                variants[str(variant["id"])] = {
                    "title": product["title"] + " / " + variant["title"],
                    "price": str(Decimal(variant["price"])),
                    "available": variant["available"],
                    "url": f"{collection.split('/collections/')[0]}/products/{product['handle']}?variant={variant['id']}",
                }
        if len(products) < 250:
            break
    else:
        raise RuntimeError("Catalogue exceeded the pagination limit")
    if not variants and not allow_empty:
        raise RuntimeError("Retailer returned an empty catalogue")
    return variants


def scan_level_up(store):
    collections = (
        "pokemon-pre-orders",
        "japanese-pokemon-tcg",
        "pokemon-chinese",
        "korean-pokemon",
        "pokemon-booster-packs",
        "pokemon-blisters",
        "pokemon-booster-boxes",
        "pokemon-elite-trainer-boxes",
        "pokemon-boxes",
        "pokemon-tins",
        "pokemon-decks",
    )
    items = {}
    for collection in collections:
        items.update(scan_shopify(f"{store}/collections/{collection}", allow_empty=True))
    if not items:
        raise RuntimeError("Retailer returned an empty catalogue")
    return items


def scan_geek_home(store):
    url = f"{store}/wp-json/wc/store/v1/products/categories?slug=pokemon"
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=12) as response:
        categories = json.load(response)
    category = next((item for item in categories if item["slug"] == "pokemon"), None)
    if category is None:
        raise RuntimeError("Pokémon category unavailable")
    return scan_woocommerce(store, category["id"])


def scan_woocommerce(store, category=None):
    items = {}
    for page in range(1, 101):
        url = f"{store}/wp-json/wc/store/v1/products?per_page=100&page={page}"
        if category is not None:
            url += f"&category={int(category)}"
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=12) as response:
            products = json.load(response)
            last_page = int(response.headers.get("X-WP-TotalPages", page + 1))
        for product in products:
            prices = product["prices"]
            if prices["currency_code"] != "ZAR" or not prices["price"]:
                continue
            price = Decimal(prices["price"]).scaleb(-int(prices["currency_minor_unit"]))
            items[str(product["id"])] = {
                "title": product["name"],
                "price": str(price),
                "available": product["is_in_stock"],
                "url": product["permalink"],
            }
        if page >= last_page or len(products) < 100:
            break
    else:
        raise RuntimeError("Catalogue exceeded the pagination limit")
    if not items:
        raise RuntimeError("Retailer returned no priced products")
    return items


def changes(previous, current):
    for key, item in current.items():
        old = previous.get(key)
        reasons = []
        if old is None:
            reasons.append("New listing" if item["available"] else "New listing (out of stock)")
        else:
            if item["available"] and not old["available"]:
                reasons.append("Restocked")
            if Decimal(item["price"]) < Decimal(old["price"]):
                reasons.append(f"Price dropped from R{old['price']}")
        if reasons:
            yield f"{', '.join(reasons)}: {item['title']} (R{item['price']})\n{item['url']}"


def main():
    if "--test-email" in sys.argv:
        send("Poke-IT SA stock alerts test", "Your private stock alert email is connected.")
        return
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if state and "stores" not in state:
        state = {"stores": {"Pokémon South Africa": state}}
    stores = state.setdefault("stores", {})
    alerts = []
    successful = 0
    readers = {
        "shopify": scan_shopify,
        "woocommerce": scan_woocommerce,
        "shopify_collections": scan_level_up,
        "woocommerce_category": scan_geek_home,
    }
    for name, (platform, url) in SOURCES.items():
        try:
            current = readers[platform](url)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, DecimalException) as error:
            print(f"::warning::{name}: {error}")
            continue
        successful += 1
        if name in stores:
            alerts.extend(f"{name} — {entry}" for entry in changes(stores[name], current))
        stores.setdefault(name, {}).update(current)
        print(f"{name}: checked {len(current)} items")
    if not successful:
        raise RuntimeError("No retailer scans succeeded")
    if alerts:
        send(f"Poke-IT SA stock alerts ({len(alerts)})", "\n\n".join(alerts))
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
