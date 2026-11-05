import json
import os
import smtplib
import ssl
import sys
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen

COLLECTION = "https://www.pokemonsouthafrica.store/collections/pokemon-tcg-products-english"
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


def scan():
    variants = {}
    for page in range(1, 51):
        request = Request(f"{COLLECTION}/products.json?limit=250&page={page}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=25) as response:
            products = json.load(response)["products"]
        for product in products:
            for variant in product["variants"]:
                variants[str(variant["id"])] = {
                    "title": product["title"] + " / " + variant["title"],
                    "price": str(Decimal(variant["price"])),
                    "available": variant["available"],
                    "url": f"{COLLECTION.split('/collections/')[0]}/products/{product['handle']}?variant={variant['id']}",
                }
        if len(products) < 250:
            break
    else:
        raise RuntimeError("Catalogue exceeded the pagination limit")
    if not variants:
        raise RuntimeError("Retailer returned an empty catalogue")
    return variants


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
    current = scan()
    previous = json.loads(STATE.read_text()) if STATE.exists() else None
    alerts = list(changes(previous, current)) if previous is not None else []
    if alerts:
        send(f"Poke-IT SA stock alerts ({len(alerts)})", "\n\n".join(alerts))
    state = previous or {}
    state.update(current)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    print(f"Checked {len(current)} variants. {len(alerts)} changes.")


if __name__ == "__main__":
    main()
