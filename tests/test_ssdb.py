"""SSDB unit tests."""
import time
import json
from configparser import ConfigParser
from pytest import mark
from ssdb import Address, ServerList, ServerData, _parse_ms_response, address_equals, \
    _address_to_str, parse_config, _parse_ips


@mark.parametrize("a,b", [
    (("127.0.0.1", 27015), ("127.0.0.1", 27015)),
    (("127.0.0.1", 0), ("127.0.0.1", 27015)),
    (("127.0.0.1", 27015), ("127.0.0.1", 0))
])
def test_address_equals(a, b):
    """Addresses equals?"""
    assert address_equals(a, b)
    assert address_equals(b, a)


@mark.parametrize("a,b", [
    (("127.0.0.1", 27015), ("127.0.0.2", 27015)),
    (("127.0.0.1", 27015), ("127.0.0.1", 27016)),
    (("127.0.0.1", 0), ("127.0.0.2", 27015)),
    (("127.0.0.2", 27015), ("127.0.0.1", 0))
])
def test_address_equals_not(a, b):
    """Addresses do not equal?"""
    assert address_equals(a, b) is False
    assert address_equals(b, a) is False


@mark.parametrize("val,expected", [
    (("127.0.0.1", 27015), "127.0.0.1:27015"),
    (("127.0.0.1", 0), "127.0.0.1")
])
def test_address_to_str(val, expected):
    """Address to string"""
    assert _address_to_str(val) == expected


@mark.parametrize("a,b", [
    (ServerData(("127.0.0.1", 27015)), ServerData(("127.0.0.1", 27015))),
    (ServerData(("127.0.0.2", 27015)), ServerData(("127.0.0.2", 27015))),
    (ServerData(("127.0.0.3", 27016)), ServerData(("127.0.0.3", 27016))),
])
def test_serverdata_equals(a, b):
    """Server data equals?"""
    assert a.equals(b)
    assert b.equals(a)


@mark.parametrize("a,b", [
    (ServerData(("127.0.0.1", 27015)), ServerData(("127.0.0.2", 27015))),
    (ServerData(("127.0.0.1", 27015)), ServerData(("127.0.0.1", 27016)))
])
def test_serverdata_equals_not(a, b):
    """Server data not equal?"""
    assert a.equals(b) is False
    assert b.equals(a) is False


def test_serverdata_differs():
    """Server data differs"""
    srv1 = ServerData(("127.0.0.1", 27015), ply_count=10,
                      max_ply_count=12, server_name="name", map_name="map")
    srv2 = ServerData(("127.0.0.1", 27015), ply_count=10,
                      max_ply_count=12, server_name="name", map_name="map")
    assert srv1.differs(srv2) is False

    srv2._ply_count = 11  # pylint: disable=W0212
    assert srv1.differs(srv2)

    srv2._ply_count = 10  # pylint: disable=W0212
    srv2._max_ply_count = 13  # pylint: disable=W0212
    assert srv1.differs(srv2)

    srv2._max_ply_count = 12  # pylint: disable=W0212
    srv2._server_name = "name2"  # pylint: disable=W0212
    assert srv1.differs(srv2)

    srv2._server_name = "name"  # pylint: disable=W0212
    srv2._map_name = "map2"  # pylint: disable=W0212
    assert srv1.differs(srv2)


def test_serverlist_update():
    """Server list update"""
    cur_list = ServerList([ServerData(("127.0.0.1", 27015)),
                           ServerData(("127.0.0.2", 27015))])
    new_list = [ServerData(("127.0.0.1", 27015)),
                ServerData(("127.0.0.2", 27015))]

    assert cur_list.update(new_list) is False


def test_serverlist_update_empty():
    """Update empty server list"""
    cur_list = ServerList()
    new_list = [ServerData(("127.0.0.1", 27015))]

    assert cur_list.update(new_list)
    assert len(cur_list.get_addresses()) == 1


def test_serverlist_update_unresponsive():
    """Update server list with unresponsive server"""
    cur_list = ServerList([ServerData(("127.0.0.1", 27015))])

    assert cur_list.update([], 0.1) is False
    assert len(cur_list.get_addresses()) == 1

    time.sleep(0.3)
    assert cur_list.update([], 0.1)
    assert len(cur_list.get_addresses()) == 0


def test_serverlist_update_responsive():
    """Update server list with responsive server"""
    srv1 = ServerData(("127.0.0.1", 27015))
    srv1.set_unresponsive()

    cur_list = ServerList([srv1])
    new_list = [ServerData(("127.0.0.1", 27015))]

    assert cur_list.update(new_list, 0.0) is False
    assert len(cur_list.get_addresses()) == 1


def test_serverlist_update_new():
    """Update server list with new server"""
    cur_list = ServerList([ServerData(("127.0.0.1", 27015)),
                           ServerData(("127.0.0.2", 27015))])
    new_list = [ServerData(("127.0.0.1", 27015)),
                ServerData(("127.0.0.2", 27015)),
                ServerData(("127.0.0.3", 27015))]

    assert cur_list.update(new_list)
    assert len(cur_list.get_addresses()) == 3
    assert ("127.0.0.3", 27015) in cur_list.get_addresses()


def test_serverlist_update_data():
    """Update server list with new server data"""
    srv1 = ServerData(("127.0.0.1", 27015), ply_count=10,
                      max_ply_count=12, server_name="name", map_name="map")
    srv2 = ServerData(("127.0.0.1", 27015), ply_count=10,
                      max_ply_count=12, server_name="name", map_name="map")

    cur_list = ServerList([srv1])
    new_list = [srv2]

    assert cur_list.update(new_list) is False
    assert len(cur_list.get_addresses()) == 1

    srv2._ply_count = 11  # pylint: disable=W0212
    assert cur_list.update(new_list)
    assert len(cur_list.get_addresses()) == 1


def test_parse_ips():
    """Parse addresses from string"""
    lst = _parse_ips("127.0.0.1:27015,127.0.0.2")
    assert ("127.0.0.1", 27015) in lst
    assert ("127.0.0.2", 0) in lst


def test_parse_config():
    """Parse SSDB config"""
    prsr = ConfigParser()
    prsr.read_string("""
[config]
token=token
channel=1
serverlist=127.0.0.1:27015,127.0.0.2:27016
embed_title=embed_title
embed_color=0x101010
embed_max=2
server_query_interval=3
max_total_query_time=4
max_new_msgs=6
max_unresponsive_time=7
upper_format=upper_format
lower_format=lower_format
logging=WARNING
[masterserver]
appid=440
gamedir=gamedir
limit=222
steam_webapi_key=steam_webapi_key
blacklist=127.0.0.3,127.0.0.4:27015
""")
    config = parse_config(prsr)
    assert config.token == "token"
    assert config.channel_id == 1
    assert ("127.0.0.1", 27015) in config.whitelist
    assert ("127.0.0.2", 27016) in config.whitelist
    assert config.gamedir == "gamedir"
    assert config.steam_webapi_key == "steam_webapi_key"
    assert ("127.0.0.3", 0) in config.blacklist
    assert ("127.0.0.4", 27015) in config.blacklist
    assert config.embed_title == "embed_title"
    assert config.embed_color == 0x101010
    assert config.embed_max == 2
    assert config.server_query_interval == 3.0
    assert config.max_total_query_time == 4.0
    assert config.max_new_msgs == 6
    assert config.max_unresponsive_time == 7.0
    assert config.upper_format == "upper_format"
    assert config.lower_format == "lower_format"
    assert config.log_level == "WARNING"
    assert config.appid == 440
    assert config.limit == 222


def test_parse_config_serverlist():
    """Parse minimal SSDB config w/ server list method"""
    prsr = ConfigParser()
    prsr.read_string("""
[config]
token=token
channel=1
serverlist=127.0.0.1:27015
upper_format=upper_format
lower_format=lower_format
""")
    config = parse_config(prsr)
    assert config.token == "token"
    assert config.channel_id == 1
    assert ("127.0.0.1", 27015) in config.whitelist


def test_parse_config_masterserver():
    """Parse minimal SSDB config w/ master server method"""
    prsr = ConfigParser()
    prsr.read_string("""
[config]
token=token
channel=1
upper_format=upper_format
lower_format=lower_format
[masterserver]
appid=440
gamedir=gamedir
steam_webapi_key=steam_webapi_key
""")
    config = parse_config(prsr)
    assert config.token == "token"
    assert config.channel_id == 1
    assert config.appid == 440
    assert config.gamedir == "gamedir"
    assert config.steam_webapi_key == "steam_webapi_key"


@mark.parametrize("jsn,expected", [
    ("""
    {
        "response": {
            "servers": [
                {
                    "addr": "127.0.0.1:27015"
                },
                {
                    "addr": "127.0.0.2:27018"
                }
            ]
        }
    }
    """, [('127.0.0.1', 27015), ('127.0.0.2', 27018)])
])
def test_parse_ms_response(jsn: str, expected: list[Address]):
    """Parse master server response."""
    obj = json.loads(jsn)
    result = _parse_ms_response(obj)
    assert result
    assert len(result) == len(expected)
    for addr in expected:
        assert addr in result
