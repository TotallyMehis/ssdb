"""Source Servers Discord Bot"""
from collections.abc import Iterable
from dataclasses import dataclass
import time
import configparser
from os import path
import socket
import logging
import asyncio
import discord
import a2s
import requests


logger = logging.getLogger(__name__)

Address = tuple[str, int]


class ServerList():
    """Server list, handles updating & unresponsive servers."""

    def __init__(self, servers: list['ServerData'] = None):
        self._servers = servers or []

    @property
    def servers(self):
        """Server list."""
        return self._servers

    def update(self, new_list: list['ServerData'], max_unresponsive_time: float | int = 0.0):
        """Update server list."""
        insert: list[ServerData] = []
        not_found: list[ServerData] = []
        removed = 0
        updated = 0

        # Find all unresponsive servers.
        for srv in self.servers:
            found = False
            for new_srv in new_list:
                if srv.equals(new_srv):
                    found = True
                    break
            if not found:
                not_found.append(srv)

        # Find all new servers and update existing ones.
        for new_srv in new_list:
            found = False
            for srv in self.servers:
                if srv.equals(new_srv):
                    srv.set_responsive()

                    if srv.differs(new_srv):
                        updated = updated + 1
                        srv.copy(new_srv)

                    found = True
                    break
            if not found:
                insert.append(new_srv)

        # Insert new ones
        self._servers.extend(insert)

        # Update unresponsive servers.
        for srv in not_found:
            srv.set_unresponsive()

            # Remove them from list
            if max_unresponsive_time >= 0:
                unresp_time = time.time() - srv.unresponsive_start_time
                if unresp_time > max_unresponsive_time:
                    logger.info(
                        "Removing unresponsive server %s (%s) from list.",
                        _address_to_str(srv.address),
                        srv.server_name)
                    self._servers.remove(srv)
                    removed = removed + 1

        if updated > 0 or removed > 0 or len(insert) > 0:
            logger.info("New: %d | Removed: %d | Updated: %d servers", len(
                insert), removed, updated)
            return True

        return False

    def get_addresses(self):
        """Returns addresses in the server list."""
        return [srv.address for srv in self.servers]


class ServerData():
    """Holds server information."""

    def __init__(self, address: Address, ply_count=0, max_ply_count=0, server_name="", map_name=""):
        self.address = address
        self._ply_count = ply_count
        self._max_ply_count = max_ply_count
        self._server_name = server_name
        self._map_name = map_name
        # When we lost connection to server for the first time
        self._unresponsive_start_time: float | int = 0

    @property
    def ply_count(self):
        """Player count"""
        return self._ply_count

    @property
    def max_ply_count(self):
        """Max players"""
        return self._max_ply_count

    @property
    def server_name(self):
        """Server name"""
        return self._server_name

    @property
    def map_name(self):
        """Map name"""
        return self._map_name

    @property
    def unresponsive_start_time(self):
        """When we lost connection to server for the first time."""
        return self._unresponsive_start_time

    @property
    def is_unresponsive(self):
        """Is server unresponsive"""
        return self._unresponsive_start_time != 0

    @property
    def full_socket(self):
        """IP:PORT"""
        return f"{self.address[0]}:{self.address[1]}"

    def equals(self, srv: 'ServerData'):
        """Is the same server?"""
        if srv == self:
            return True

        if self.full_socket == srv.full_socket:
            return True

        return False

    def differs(self, srv: 'ServerData'):
        """Server data differs?"""
        if self.ply_count != srv.ply_count:
            return True
        if self.max_ply_count != srv.max_ply_count:
            return True
        if self.server_name != srv.server_name:
            return True
        if self.map_name != srv.map_name:
            return True

        return False

    def copy(self, srv: 'ServerData'):
        """Copy data"""
        self._ply_count = srv.ply_count
        self._max_ply_count = srv.max_ply_count
        self._server_name = srv.server_name
        self._map_name = srv.map_name

    def set_from_info(self, info: a2s.SourceInfo):
        """Update data from A2S."""
        # Ignore bots if possible.
        self._ply_count = info.player_count - info.bot_count
        self._max_ply_count = info.max_players
        self._server_name = info.server_name
        self._map_name = info.map_name

    def set_unresponsive(self):
        """Set server as unresponsive."""
        if not self.is_unresponsive:
            self._unresponsive_start_time = time.time()

    def set_responsive(self):
        """Set server as responsive."""
        self._unresponsive_start_time = 0


class _QuerierInterface():
    """Querier interface."""

    def query_servers(self, addresses: list[Address],
                      max_total_query_time: float) -> list[ServerData]:
        """Query a list of game servers."""

    def query_masterserver(self, webapi_key: str, appid: int, gamedir: str, limit: int,
                           max_ms_query_time: float | int) -> list[Address]:
        """Queries the Source master server list and returns all addresses found. Should keep these
        queries to the minimum, or you get timed out."""


class QuerierImpl(_QuerierInterface):
    """Querier implementation."""

    def query_servers(self, addresses: list[Address],
                      max_total_query_time: float) -> list[ServerData]:
        return _query_servers(addresses, max_total_query_time)

    def query_masterserver(self, webapi_key: str, appid: int, gamedir: str, limit: int,
                           max_ms_query_time: float | int) -> list[Address]:
        return _query_masterserver(webapi_key, appid, gamedir, limit, max_ms_query_time)


@dataclass
class SSDBConfig:
    """SSDB configuration"""
    token: str = ""
    """Discord token"""
    channel_id: int = 0
    """Discord channel id"""
    whitelist: list[Address] | None = None
    appid: int | None = None
    gamedir: str | None = None
    limit: int | None = None
    steam_webapi_key: str = ""
    blacklist: list[Address] | None = None
    embed_title: str = ""
    embed_max: int = 5
    embed_color: int = 0xFFFFFF
    max_total_query_time: float = 0.0
    query_interval: float = 0.0
    server_query_interval: float = 0.0
    max_new_msgs: int = 5
    max_unresponsive_time: float = 0.0
    upper_format: str = ""
    lower_format: str = ""
    log_level: str = "INFO"


class QuerySystem():
    """Query System."""

    def __init__(self,
                 querier: _QuerierInterface = QuerierImpl(),
                 max_ms_query_time: float | None = None,
                 whitelist: list[Address] | None = None,
                 appid: int | None = None,
                 gamedir: str | None = None,
                 limit: int | None = None,
                 webapi_key: str | None = None,
                 blacklist: list[Address] | None = None,
                 query_interval: float | None = None,
                 max_unresponsive_time: float | None = None,
                 ms_query_interval: float | None = None,
                 max_total_query_time: float | int | None = None):
        assert whitelist or (appid and gamedir), \
            "You must have serverlist (a whitelist) or appid+gamedir configured!"
        if not whitelist:
            assert webapi_key, "You must have webapi_key configured!"
        self._num_offline = 0
        self._querier = querier
        self._server_list = ServerList()
        self._whitelist = whitelist
        self._appid = appid
        self._gamedir = gamedir
        self._limit = limit or 100
        self._webapi_key = webapi_key
        self._blacklist = blacklist or []
        self._max_ms_query_time = max_ms_query_time or 30.0
        self._query_interval = query_interval or 30.0
        self._max_unresponsive_time = max_unresponsive_time or 60.0
        self._ms_query_interval = ms_query_interval or 100.0
        self._max_total_query_time = max_total_query_time or 30.0
        self._last_query_time = 0.0
        self._last_ms_query_time = 0.0

    @property
    def server_list(self):
        """Returns the current server list."""
        return self._server_list

    @property
    def num_offline(self):
        """Number of offline servers."""
        return self._num_offline

    async def update(self, loop: asyncio.AbstractEventLoop):
        """Returns whether the server list was updated."""
        if self._should_query():
            new_lst = await self._query_new_list(loop)
            self._last_query_time = time.time()
            return self._server_list.update(new_lst, self._max_unresponsive_time)
        return False

    def _should_query(self):
        return (time.time() - self._last_query_time) >= self._query_interval

    async def _query_new_list(self, loop: asyncio.AbstractEventLoop):
        """Returns the server list depending on the configuration options."""
        new_lst = None
        if self._whitelist:
            # User wants a specific list from ips.
            self._num_offline = 0
            new_lst = await loop.run_in_executor(
                None,
                self._querier.query_servers, self._whitelist, self._max_total_query_time)
            self._num_offline = len(self._whitelist) - len(new_lst)
        elif self._should_query_last_list():
            # Query the servers we've already collected.
            addresses = self._server_list.get_addresses()
            new_lst = await loop.run_in_executor(
                None,
                self._querier.query_servers, addresses, self._max_total_query_time)
        else:
            # Query new list from master server.
            assert self._gamedir
            addresses = await loop.run_in_executor(
                None,
                self._querier.query_masterserver,
                self._webapi_key, self._appid, self._gamedir, self._limit, self._max_ms_query_time)
            addresses = [
                address for address in addresses if not self._is_blacklisted(address)]
            self._last_ms_query_time = time.time()

            new_lst = await loop.run_in_executor(
                None,
                self._querier.query_servers, addresses, self._max_total_query_time)

        return new_lst

    def _is_blacklisted(self, address: Address):
        for blacklisted in self._blacklist:
            if address_equals(blacklisted, address):
                return True
        return False

    def _should_query_last_list(self):
        if not self._server_list.servers:
            return False
        return (time.time() - self._last_ms_query_time) <= self._ms_query_interval


def _parse_ips(ip_list: str):
    """Parse IP addresses from config."""
    if not ip_list:
        return None

    lst: list[Address] = []

    for address in ip_list.split(","):
        ip = address.split(":")
        ip[0] = ip[0].strip()

        if not ip[0]:
            continue

        ip_port = 0 if len(ip) <= 1 else int(ip[1])

        logger.debug("Parsed ip %s (%d)!", ip[0], ip_port)
        lst.append((ip[0], ip_port))

    return lst


def _value_cap_min(value: float | int, minval: float | int, def_value: float | int):
    if value > minval:
        return value
    else:
        return def_value


def _address_to_str(address: Address):
    if address[1] == 0:  # No port
        return address[0]
    return f"{address[0]}:{address[1]}"


def address_equals(a1: Address, a2: Address):
    """Address equals? Port value 0 is considered as 'any' port."""
    if a1[0] == a2[0]:
        if a1[1] == 0 or a2[1] == 0:
            return True
        if a1[1] == a2[1]:
            return True
    return False


def _query_server_info(address: Address) -> a2s.SourceInfo | None:
    logger.debug("Querying server %s...", _address_to_str(address))

    try:
        info = a2s.info(address)
        return info
    except socket.timeout:
        logger.info(
            "Couldn't contact server %s.", _address_to_str(address))
    except (a2s.BrokenMessageError,
            a2s.BufferExhaustedError,
            socket.gaierror,
            ConnectionError,
            OSError) as e:
        logger.error(
            "Connection error querying server: %s", e)

    return None


def _query_servers(addresses: list[Address], max_total_query_time: float):
    logger.info("Querying %d servers...", len(addresses))

    lst: list[ServerData] = []
    query_start = time.time()

    for address in addresses:
        info = _query_server_info(address)
        if info:
            srv = ServerData(address)
            srv.set_from_info(info)
            lst.append(srv)

        if (time.time() - query_start) > max_total_query_time:
            break

    return lst


def _query_masterserver(webapi_key: str,
                        appid: int,
                        gamedir: str,
                        limit: int,
                        max_ms_query_time: float | int) -> list[Address]:
    logger.info("Querying masterserver...")

    try:
        response = requests.get(
            "https://api.steampowered.com/IGameServersService/GetServerList/v1/",
            params={
                "key": webapi_key,
                "format": "json",
                "filter": f"\\appid\\{appid}\\gamedir\\{gamedir}",
                "limit": f"{limit}"
            },
            timeout=max_ms_query_time)

        if not response.ok:
            logger.error(
                "Master server returned an error. Response: %s", response)
            return []

        obj = response.json()

        parsed = _parse_ms_response(obj)
        if not parsed:
            logger.error("Failed to parse master server response: %s", obj)
            return []
        return parsed
    except Exception as e:  # pylint: disable=W0718
        logger.error(
            "Failed to query master server: %s", e)

    return []


def _parse_ms_response(json):
    if "response" not in json:
        return None
    if "servers" not in json["response"]:
        return None
    if not isinstance(json["response"]["servers"], Iterable):
        return None
    lst: list[Address] | None = None
    for server in json["response"]["servers"]:
        if "addr" not in server:
            continue
        splt = str(server["addr"]).rsplit(":", maxsplit=1)
        ip = splt[0]
        port = int(splt[1])
        if lst is None:
            lst = []
        lst.append((ip, port))
    return lst


def _get_persistent_last_msg_path():
    return path.join(
        path.dirname(__file__), ".persistent_lastmsg.txt")


def read_persisted_msg_id():
    """Read persisted message id."""
    file_name = _get_persistent_last_msg_path()
    try:
        with open(file_name, "r", encoding="utf-8") as fp:
            return int(fp.read())
    except IOError:
        pass
    return None


def write_persistent_msg_id(msg_id: int):
    """Write message id to file for persistency."""
    file_name = _get_persistent_last_msg_path()
    with open(file_name, "w", encoding="utf-8") as fp:
        fp.write(str(msg_id) + "\n")


def _build_list_embed(lst: ServerList, config: "SSDBConfig", num_offline: int):
    # Sort according to player count
    servers = sorted(
        lst.servers,
        key=lambda srv: srv.ply_count,
        reverse=True)
    # I just had a deja vu...
    # ABOUT THIS EXACT CODE AND ME EXPLAINING IT IN THIS COMMENT
    # FREE WILL IS A LIE
    # WE LIVE IN A SIMULATION
    description = f"{len(servers)} server(s) online"

    if num_offline > 0:
        description += ", {num_offline} offline"

    description += f"\nUpdating every {config.server_query_interval:.0f} seconds"

    em = discord.Embed(
        title=config.embed_title,
        description=description,
        colour=config.embed_color)
    counter = 0
    for srv in servers:
        kwargs = {
            "name": srv.server_name,
            "address": srv.full_socket,
            "map": srv.map_name,
            "players": srv.ply_count,
            "max_players": srv.max_ply_count
        }

        em.add_field(
            name=config.upper_format.format(**kwargs),
            value=config.lower_format.format(**kwargs),
            inline=False)

        counter += 1
        if counter >= config.embed_max:
            break

    return em


def parse_config(prsr: configparser.ConfigParser):
    """Parse config."""
    token = prsr.get("config", "token", fallback=None)
    channel_id = prsr.getint("config", "channel", fallback=None)
    whitelist = _parse_ips(prsr.get("config", "serverlist", fallback=None))
    try:
        appid = prsr.getint("masterserver", "appid", fallback=None)
    except ValueError:
        appid = None
    gamedir = prsr.get("masterserver", "gamedir", fallback=None)
    try:
        limit = prsr.getint("masterserver", "limit", fallback=None)
    except ValueError:
        limit = None
    steam_webapi_key = prsr.get(
        "masterserver", "steam_webapi_key", fallback=None)

    assert token and channel_id, "You must configure Discord token and channel!"
    assert whitelist or (appid and gamedir), \
        "You must configure one list method, 'serverlist' or ('appid'+'gamedir')!"
    if gamedir:
        assert steam_webapi_key, "You must configure 'steam_webapi_key' if you are using 'gamedir'!"

    embed_title = prsr.get("config", "embed_title", fallback="Servers")

    embed_max = prsr.getint("config", "embed_max", fallback=1)
    embed_max = 1 if embed_max < 1 else embed_max

    embed_color = int(prsr.get(
        "config", "embed_color", fallback="0x0"), base=16)

    max_total_query_time = _value_cap_min(
        prsr.getfloat("config",
                      "max_total_query_time",
                      fallback=30),
        0, 30)
    query_interval = _value_cap_min(
        prsr.getfloat("config",
                      "query_interval",
                      fallback=100),
        0, 100)
    server_query_interval = _value_cap_min(
        prsr.getfloat("config",
                      "server_query_interval",
                      fallback=20),
        0, 20)
    max_new_msgs = prsr.getint("config", "max_new_msgs", fallback=5)
    max_unresponsive_time = prsr.getfloat(
        "config", "max_unresponsive_time", fallback=0)
    upper_format = prsr.get("config", "upper_format")
    lower_format = prsr.get("config", "lower_format")
    blacklist = _parse_ips(
        prsr.get("masterserver", "blacklist", fallback=None))
    log_level = prsr.get("config", "logging", fallback="").upper()
    return SSDBConfig(token=token, channel_id=channel_id, appid=appid, gamedir=gamedir, limit=limit,
                      steam_webapi_key=steam_webapi_key, whitelist=whitelist,
                      embed_title=embed_title, embed_max=embed_max, embed_color=embed_color,
                      max_total_query_time=max_total_query_time,
                      query_interval=query_interval,
                      server_query_interval=server_query_interval,
                      max_new_msgs=max_new_msgs,
                      max_unresponsive_time=max_unresponsive_time,
                      upper_format=upper_format, lower_format=lower_format,
                      blacklist=blacklist, log_level=log_level)
