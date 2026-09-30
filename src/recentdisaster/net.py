import httpx

from recentdisaster.config import USER_AGENT


def client(timeout: float = 20) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "id,en;q=0.8"},
        timeout=timeout,
        follow_redirects=True,
        transport=httpx.HTTPTransport(retries=1),
    )


def get_text(c: httpx.Client, url: str) -> str:
    r = c.get(url)
    r.raise_for_status()
    return r.text
