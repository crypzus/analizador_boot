"""Envio a Telegram. El token solo se lee del entorno y nunca se imprime."""
from __future__ import annotations

import os

import requests


def send(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                          data={"chat_id": chat, "text": text[:4000]})
        return r.ok
    except requests.RequestException as e:
        # el mensaje de la excepcion puede contener la URL con el token: solo mostramos el tipo
        print(f"[telegram] fallo al enviar: {type(e).__name__}")
        return False
