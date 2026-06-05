import asyncio
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

async def test():
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": 'Является ли следующее сообщение спамом или вредоносным? Ответь только "да" или "нет".\nСообщение: Казино! Заработай 100000 рублей за один день! Переходи по ссылке!'}],
        "max_tokens": 10,
        "temperature": 0,
    }

    print(f"URL: {url}")
    print(f"Model: {LLM_MODEL}")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            print(f"Статус ответа: {resp.status}")
            data = await resp.json()
            answer = data["choices"][0]["message"]["content"].strip()
            print(f"Ответ LLM: '{answer}'")
            print(f"Определено как спам: {'да' in answer.lower()[:10]}")

asyncio.run(test())