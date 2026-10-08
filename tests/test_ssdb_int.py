"""SSDB integration tests."""
import asyncio
import time
from ssdb import _QuerierInterface, Address, QuerySystem, ServerData, ServerList


class _TestQuerier(_QuerierInterface):
    _ADDR_TO_RETURN = [("127.0.0.1", 27015),
                       ("127.0.0.2", 27015),
                       ("127.0.0.2", 27016)]

    def query_servers(self, addresses: list[Address],
                      max_total_query_time: float) -> list[ServerData]:
        return [ServerData(addr) for addr in addresses if addr in self._ADDR_TO_RETURN]

    def query_masterserver(self, webapi_key: str, appid: int, gamedir: str, limit: int,
                           max_ms_query_time: float | int) -> list[Address]:
        return self._ADDR_TO_RETURN


def test_querysystem_list():
    """Query specific list."""
    query_system = QuerySystem(querier=_TestQuerier(), whitelist=[
                               ("127.0.0.1", 27015), ("127.0.0.2", 27015)])

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())

    assert updated
    addresses = query_system.server_list.get_addresses()
    assert len(addresses) == 2
    assert ("127.0.0.1", 27015) in addresses
    assert ("127.0.0.2", 27015) in addresses


def test_querysystem_ms():
    """Query masterserver using gamedir."""
    query_system = QuerySystem(querier=_TestQuerier(
    ), gamedir="cstrike", appid=240, webapi_key="webapi_key")

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())

    assert updated
    addresses = query_system.server_list.get_addresses()
    assert ("127.0.0.1", 27015) in addresses
    assert ("127.0.0.2", 27015) in addresses
    assert ("127.0.0.2", 27016) in addresses


def test_querysystem_blacklist_port():
    """Blacklist a server with a port."""
    query_system = QuerySystem(querier=_TestQuerier(
    ), gamedir="cstrike", appid=240, webapi_key="webapi_key", blacklist=[("127.0.0.2", 27015)])

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())

    assert updated
    addresses = query_system.server_list.get_addresses()
    assert ("127.0.0.1", 27015) in addresses
    assert ("127.0.0.2", 27015) not in addresses
    assert ("127.0.0.2", 27016) in addresses


def test_querysystem_blacklist_noport():
    """Blacklist a server without a port."""
    query_system = QuerySystem(querier=_TestQuerier(
    ), gamedir="cstrike", appid=240, webapi_key="webapi_key", blacklist=[("127.0.0.3", 0)])

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())

    assert updated
    addresses = query_system.server_list.get_addresses()
    assert ("127.0.0.1", 27015) in addresses
    assert ("127.0.0.3", 27015) not in addresses
    assert ("127.0.0.3", 27016) not in addresses


def test_querysystem_update_interval():
    """Update interval works."""
    query_system = QuerySystem(querier=_TestQuerier(
    ), gamedir="cstrike", appid=240, webapi_key="webapi_key", query_interval=0.25)

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())
    assert updated

    # Reset server list.
    query_system._server_list = ServerList()  # pylint: disable=W0212
    updated = asyncio.run(run_co())
    assert updated is False

    time.sleep(0.3)  # Sleep until next update.
    updated = asyncio.run(run_co())
    assert updated


def test_querysystem_offline():
    """Offline servers."""
    query_system = QuerySystem(querier=_TestQuerier(
    ), whitelist=[("127.0.0.1", 27015), ("127.0.0.2", 27015), ("127.0.0.3", 27015)])

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())
    assert updated
    assert query_system.num_offline == 1
    addresses = query_system.server_list.get_addresses()
    assert ("127.0.0.1", 27015) in addresses
    assert ("127.0.0.2", 27015) in addresses
    assert ("127.0.0.3", 27015) not in addresses


def test_querysystem_last_list_ms():
    """Query last list and then master server again."""
    query_system = QuerySystem(
        querier=_TestQuerier(),
        gamedir="cstrike",
        appid=240,
        webapi_key="webapi_key",
        query_interval=-0.1,  # Query constantly
        ms_query_interval=0.1)  # Query master server after 0.1 seconds.

    async def run_co():
        loop = asyncio.get_event_loop()
        return await query_system.update(loop)

    updated = asyncio.run(run_co())
    assert updated
    addresses = query_system.server_list.get_addresses()
    assert len(addresses) == 3

    # Reset server list.
    query_system._server_list = ServerList()  # pylint: disable=W0212

    updated = asyncio.run(run_co())
    assert updated
    addresses = query_system.server_list.get_addresses()
    assert len(addresses) == 3
