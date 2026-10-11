import asyncio

import httpx

from daybook.model import ModelConfig, ModelGateway


def gateway(handler) -> ModelGateway:
    gw = ModelGateway(ModelConfig())
    gw._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://mock/v1/"
    )
    return gw


async def test_startup_check_finds_model_before_any_generation():
    checked = asyncio.Event()
    paths = []

    async def transport(request):
        paths.append(request.url.path)
        checked.set()
        return httpx.Response(200, json={"data": [{"id": "qwen3.6:35b"}]})

    gw = gateway(transport)
    try:
        assert not gw.status().reachable
        gw.check_health()
        await asyncio.wait_for(checked.wait(), 0.3)
        await asyncio.sleep(0)
        assert gw.status().reachable
        assert paths == ["/v1/models"]
        assert gw.status().calls_per_minute == 0
    finally:
        await gw.close()


async def test_check_after_close_starts_no_probe():
    paths = []

    async def transport(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "qwen3.6:35b"}]})

    gw = gateway(transport)
    await gw.close()
    gw.check_health()
    assert not gw.status().reachable
    await asyncio.sleep(0.05)
    assert paths == []
