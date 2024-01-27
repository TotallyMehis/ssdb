import unittest
from ssdb import ServerList, ServerData, address_equals


class SsdbTests(unittest.TestCase):
    def test_sameserver(self):
        self.assertTrue(
            ServerData(("127.0.0.1", 27015)).equals(ServerData(("127.0.0.1", 27015)))
        )

    def test_differentserver(self):
        # Different server
        self.assertFalse(
            ServerData(("127.0.0.1", 27015)).equals(ServerData(("127.0.0.2", 27015)))
        )
        self.assertFalse(
            ServerData(("127.0.0.1", 27015)).equals(ServerData(("127.0.0.1", 27016)))
        )

    def test_samelist(self):
        lst1 = ServerList()
        lst1.add_server(ServerData(("127.0.0.1", 27015)))
        lst1.add_server(ServerData(("127.0.0.2", 27015)))
        lst2 = ServerList()
        lst2.add_server(ServerData(("127.0.0.2", 27015)))
        lst2.add_server(ServerData(("127.0.0.1", 27015)))
        self.assertTrue(lst1.equals(lst2))

    def test_differentlist(self):
        lst1 = ServerList()
        lst1.add_server(ServerData(("127.0.0.1", 27015)))
        lst1.add_server(ServerData(("127.0.0.2", 27015)))
        lst2 = ServerList()
        lst2.add_server(ServerData(("127.0.0.1", 27015)))
        lst2.add_server(ServerData(("127.0.0.2", 27016)))
        self.assertFalse(lst1.equals(lst2))

    def test_addressequals(self):
        self.assertTrue(address_equals(("127.0.0.1", 27015), ("127.0.0.1", 27015)))
        self.assertTrue(address_equals(("127.0.0.1", 0), ("127.0.0.1", 27015)))
        self.assertTrue(address_equals(("127.0.0.1", 27015), ("127.0.0.1", 0)))

    def test_addressnotequals(self):
        self.assertFalse(address_equals(("127.0.0.1", 27015), ("127.0.0.2", 27015)))
        self.assertFalse(address_equals(("127.0.0.1", 27015), ("127.0.0.1", 27016)))
        self.assertFalse(address_equals(("127.0.0.1", 0), ("127.0.0.2", 27015)))
        self.assertFalse(address_equals(("127.0.0.2", 27015), ("127.0.0.1", 0)))


if __name__ == "__main__":
    unittest.main()
