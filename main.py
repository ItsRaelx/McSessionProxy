from fastapi import FastAPI, Request, Response
import httpx
import random
import asyncio
import uvicorn
import os
import dotenv
from typing import Dict
import time
from datetime import datetime

dotenv.load_dotenv()

app = FastAPI()
PROXY_URL = os.getenv("PROXY_URL")


def log_message(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.failed_proxies: Dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.failure_timeout = 60

    async def update_proxies(self):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(PROXY_URL)
                response.raise_for_status()
                new_proxies = []

                for line in response.text.strip().splitlines():
                    parts = line.split(':')
                    if len(parts) == 4:
                        ip, port, username, password = parts
                        proxy_url = f"http://{username}:{password}@{ip}:{port}/"
                        new_proxies.append(proxy_url)

                async with self.lock:
                    self.proxies = new_proxies
                    log_message(f"✅ Proxies updated. Total proxies: {len(self.proxies)}")

            except httpx.RequestError as e:
                log_message(f"❌ Failed to download proxies: {e}")

    async def get_random_proxy(self):
        async with self.lock:
            current_time = time.time()
            self.failed_proxies = {
                proxy: timestamp
                for proxy, timestamp in self.failed_proxies.items()
                if current_time - timestamp < self.failure_timeout
            }

            available_proxies = [p for p in self.proxies if p not in self.failed_proxies]

            if available_proxies:
                proxy = random.choice(available_proxies)
                log_message(f"🔄 Selected proxy: {proxy}")
                return proxy

            log_message("⚠️ No proxies available")
            return None

    async def mark_proxy_failed(self, proxy: str):
        async with self.lock:
            self.failed_proxies[proxy] = time.time()
            log_message(f"❌ Marked proxy as failed: {proxy}")


proxy_manager = ProxyManager()


async def make_request_with_retries(request, target_url, headers, body, max_retries=3):
    log_message(f"📨 Incoming request: {request.method} {target_url}")

    for attempt in range(max_retries):
        proxy = await proxy_manager.get_random_proxy()
        if not proxy:
            log_message(f"⚠️ Attempt {attempt + 1}: No proxy available")
            continue

        try:
            async with httpx.AsyncClient(
                    proxies={"http://": proxy, "https://": proxy},
                    timeout=30.0,
                    verify=False
            ) as client:
                log_message(f"🔄 Attempt {attempt + 1}: Making request via {proxy}")

                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                    params=request.query_params,
                )

                if response.status_code == 200:
                    log_message(f"✅ Request successful - Status: {response.status_code}")
                    return response
                else:
                    await proxy_manager.mark_proxy_failed(proxy)
                    log_message(f"⚠️ Request failed - Status: {response.status_code}")
                    continue

        except Exception as e:
            log_message(f"❌ Error with proxy {proxy}: {str(e)}")
            await proxy_manager.mark_proxy_failed(proxy)

    log_message("❌ All retry attempts failed")
    return None


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_request(request: Request, path: str):
    target_url = f"https://sessionserver.mojang.com/{path}"
    body = await request.body() if request.method in ["POST", "PUT"] else None
    headers = dict(request.headers)
    headers.pop("host", None)

    response = await make_request_with_retries(request, target_url, headers, body)

    if response:
        content = await response.aread()
        headers = dict(response.headers)

        # Remove problematic headers
        headers.pop('content-encoding', None)
        headers.pop('transfer-encoding', None)

        # Set correct content length
        headers['content-length'] = str(len(content))

        return Response(
            content=content,
            status_code=response.status_code,
            headers=headers
        )
    else:
        error_content = b"All attempts failed. Unable to complete the request."
        return Response(
            content=error_content,
            status_code=500,
            headers={'content-length': str(len(error_content))}
        )


async def update_proxies_periodically():
    while True:
        await proxy_manager.update_proxies()
        await asyncio.sleep(300)


async def main():
    log_message("🚀 Starting application...")
    await proxy_manager.update_proxies()
    asyncio.create_task(update_proxies_periodically())

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())