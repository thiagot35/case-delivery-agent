"""Interactive chat client for the delivery agent API (simulates the customer channel).

Usage:
    uv run python scripts/chat_cli.py --name "Thiago Santana" --cpf "123456*****"
"""

import argparse
import sys

import httpx

DEFAULT_URL = "http://127.0.0.1:8080"


def format_order(order: dict | None) -> str:
    """Render the response's order snapshot as one line."""
    if not order:
        return "(sem pedido)"
    items = ", ".join(f"{i['quantity']}x {i['name']}" for i in order["items"]) or "sem itens"
    return f"Pedido #{order['order_id']} [{order['status']}] {items} | total R$ {order['total_price']}"


def main() -> int:
    """Run the interactive chat loop against the agent API."""
    parser = argparse.ArgumentParser(description="Chat with the Beauty Pizza agent")
    parser.add_argument("--name", required=True, help="Customer name")
    parser.add_argument("--cpf", required=True, help="Customer CPF")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Agent API base URL (default {DEFAULT_URL})")
    args = parser.parse_args()

    print("Beauty Pizza — digite sua mensagem (ou 'sair' para encerrar)\n")
    with httpx.Client(base_url=args.url, timeout=120) as client:
        while True:
            try:
                message = input("Você: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not message:
                continue
            if message.lower() in {"sair", "exit", "quit"}:
                return 0
            try:
                response = client.post(
                    "/messages",
                    json={"client_name": args.name, "client_document": args.cpf, "message": message},
                )
            except httpx.HTTPError as exc:
                print(f"[erro de conexão: {exc}]")
                continue
            if response.status_code != 200:
                print(f"[erro {response.status_code}: {response.text}]")
                continue
            body = response.json()
            print(f"\nBeauty Pizza: {body['reply']}\n")
            print(f"  ↳ {format_order(body.get('order'))}\n")


if __name__ == "__main__":
    sys.exit(main())
