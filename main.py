from fastapi import FastAPI, Request, Response
import httpx
import random
import asyncio
import uvicorn
import os
import dotenv

dotenv.load_dotenv()


app = FastAPI()

PROXY_URL = os.getenv("PROXY_URL")


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.lock = asyncio.Lock()

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
                    print(f"Proxies updated. Total proxies: {len(self.proxies)}")

            except httpx.RequestError as e:
                print(f"Failed to download proxies: {e}")

    async def get_random_proxy(self):
        async with self.lock:
            if self.proxies:
                proxy = random.choice(self.proxies)
                print(f"Using proxy: {proxy}")
                return proxy
            print("No proxies available in the list")
            return None


proxy_manager = ProxyManager()


async def make_request_with_retries(request, target_url, headers, body, max_retries=3):
    for attempt in range(max_retries):
        proxy = await proxy_manager.get_random_proxy()
        if not proxy:
            print(f"Attempt {attempt + 1}: Unable to get a proxy for the request.")
            continue

        try:
            async with httpx.AsyncClient(
                    proxies={"http://": proxy, "https://": proxy},
                    timeout=30.0  # Add timeout
            ) as client:
                response = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                    params=request.query_params,
                )
                response.raise_for_status()
                print(f"Request successful with proxy: {proxy}")
                return response

        except httpx.HTTPStatusError as e:
            print(f"Attempt {attempt + 1} with proxy {proxy} failed: {e.response.status_code} - {e.response.text}")
        except (httpx.RequestError, httpx.ProxyError, httpx.ConnectTimeout) as e:
            print(f"Attempt {attempt + 1} failed with proxy {proxy}: {str(e)}")

    return None


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_request(request: Request, path: str):
    target_url = f"https://sessionserver.mojang.com/{path}"
    body = await request.body() if request.method in ["POST", "PUT"] else None
    headers = dict(request.headers)
    headers.pop("host", None)

    response = await make_request_with_retries(request, target_url, headers, body)

    if response:
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers={key: value for key, value in response.headers.items() if key.lower() != 'content-encoding'}
        )
    else:
        return Response(content="All attempts failed. Unable to complete the request.", status_code=500)


async def update_proxies_periodically():
    while True:
        await proxy_manager.update_proxies()
        await asyncio.sleep(300)  # 5 minutes


async def main():
    # Initial proxy update
    await proxy_manager.update_proxies()

    # Start periodic updates
    asyncio.create_task(update_proxies_periodically())

    # Run the server
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())