# Standard libraries
import time
import configparser
import sys
from os import path
import socket
import logging

# Module: discord.py
import discord
from discord.ext import tasks

# Module: python-valve
import steam.game_servers

# Module: python-a2s
import a2s


LOG_FORMAT = '%(asctime)s | %(message)s'

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def value_cap_min(value, minval, def_value):
    if value > minval:
        return value
    else:
        return def_value


def address_to_str(address):
    if address[1] == 0:
        # No port
        return address[0]
    else:
        # Port exists
        return ("%s:%i" % (address[0], address[1]))


def address_equals(a1, a2):
    # Same host
    if a1[0] == a2[0]:
        # If port is 0, ignore it
        if a1[1] == 0 or a2[1] == 0:
            return True
        elif a1[1] == a2[1]:
            return True
    return False


class ServerList():
    def __init__(self):
        self.servers = []
        self.query_time = time.time()

    def add_server(self, new_srv):
        for srv in self.servers:
            if srv.equals(new_srv):
                return False

        self.servers.append(new_srv)
        return True

    def update(self, new_srv_list, max_unresponsive_time):
        insert = []
        not_found = []
        updated = 0

        self.query_time = new_srv_list.query_time

        # Find all unresponsive servers.
        for srv in self.servers:
            found = False
            for new_srv in new_srv_list.servers:
                if srv.equals(new_srv):
                    found = True
                    break
            if not found:
                not_found.append(srv)

        # Find all new servers and update existing ones.
        for new_srv in new_srv_list.servers:
            found = False
            for srv in self.servers:
                if srv.equals(new_srv):
                    if srv.should_update(new_srv):
                        updated = updated + 1

                    srv.copy(new_srv)
                    found = True
                    break
            if not found:
                insert.append(new_srv)

        # Insert new ones
        self.servers.extend(insert)

        # Update unresponsive servers.
        for srv in not_found:
            srv.set_unresponsive()

            # Remove them from list
            if max_unresponsive_time >= 0:
                unresp_time = time.time() - srv.unresponsive_time
                if unresp_time > max_unresponsive_time:
                    logger.info(
                        "Removing unresponsive server '%s' from list." %
                        (srv.server_name))
                    self.servers.remove(srv)

        if updated > 0 or len(insert) > 0 or len(not_found) > 0:
            logger.info("Updated %i servers! %i new & %i not found servers." %
                        (updated, len(insert), len(not_found)))
            return True

        return False

    def get_addresses(self):
        """Returns all addresses we should query."""
        addresses = []
        for srv in self.servers:
            addresses.append(srv.address)
        return addresses

    def equals(self, lst):
        if not lst:
            return False

        if len(lst.servers) != len(self.servers):
            return False

        for (srv1, srv2) in zip(self.servers, lst.servers):
            if not srv1.equals(srv2):
                return False

        return True


class ServerData():
    def __init__(self, address):
        self.address = address

        self.queried = False

        self.ply_count = 0
        self.max_ply_count = 0
        self.server_name = ''
        self.map_name = ''

        # When we lost connection to server for the first time
        self.unresponsive_time = 0

        self.last_query_time = 0

    def equals(self, srv):
        if srv == self:
            return True

        if self.full_socket == srv.full_socket:
            return True

        return False

    def should_update(self, srv):
        if not self.queried:
            return True

        if self.ply_count != srv.ply_count:
            return True
        if self.max_ply_count != srv.max_ply_count:
            return True
        if self.server_name != srv.server_name:
            return True
        if self.map_name != srv.map_name:
            return True

        return False

    def copy(self, srv):
        self.ply_count = srv.ply_count
        self.max_ply_count = srv.max_ply_count
        self.server_name = srv.server_name
        self.map_name = srv.map_name

        self.last_query_time = srv.last_query_time

        self.set_responsive()

    def update_info(self, info):
        # Ignore bots if possible.
        self.ply_count = info.player_count - info.bot_count
        self.max_ply_count = info.max_players
        self.server_name = info.server_name
        self.map_name = info.map_name

        self.last_query_time = time.time()

        self.queried = True

    @property
    def is_unresponsive(self):
        return self.unresponsive_time != 0

    def set_unresponsive(self):
        if not self.is_unresponsive:
            self.unresponsive_time = time.time()

    def set_responsive(self):
        self.unresponsive_time = 0

    @property
    def full_socket(self):
        return "%s:%i" % (self.address[0], self.address[1])


class ServerListConfig:
    def __init__(self, config):
        self.embed_title = config.get('config', 'embed_title')

        self.embed_max = config.getint('config', 'embed_max', fallback=1)
        self.embed_max = 1 if self.embed_max < 1 else self.embed_max

        self.embed_color = int(config.get(
            'config', 'embed_color', fallback='0x0'), base=16)

        self.gamedir = config.get('config', 'gamedir')

        self.max_total_query_time = config.getfloat(
            'config', 'max_total_query_time', fallback=30)
        self.max_total_query_time = value_cap_min(
            self.max_total_query_time, 0, 30)

        self.query_interval = config.getfloat(
            'config', 'query_interval', fallback=100)
        self.query_interval = value_cap_min(
            self.query_interval, 0, 100)

        self.server_query_interval = config.getfloat(
            'config', 'server_query_interval', fallback=20)
        self.server_query_interval = value_cap_min(
            self.server_query_interval, 0, 20)

        self.max_new_msgs = config.getint('config', 'max_new_msgs', fallback=5)

        self.max_unresponsive_time = config.getfloat(
            'config', 'max_unresponsive_time', fallback=0)


class ServerListClient(discord.Client):
    """Task: Prints an embed list of servers.
    Responds to commands (!serverlist/!servers) whenever possible."""

    def __init__(self, config):
        super().__init__()
        # The Channel ID we will use
        self.channel_id = config.getint(
            'config', 'channel', fallback=0)
        self.config = ServerListConfig(config)
        self.user_serverlist = self.parse_ips(
            config.get('config', 'serverlist', fallback=''))
        self.user_blacklist = self.parse_ips(
            config.get('config', 'blacklist', fallback=''))

        self.serverlist = ServerList()
        self.last_action_time = 0.0  # Last time we edited or printed a message
        self.last_print_time = 0.0
        self.last_query_time = 0.0
        self.last_ms_query_time = 0.0
        self.num_offline = 0  # Number of servers we couldn't contact
        self.cur_msg = None  # The message we should edit
        self.persistent_msg_id = 0
        self.num_other_msgs = 0  # How many messages between our msg and now

        self.read_persistent_last_msg()

        self.update_task.start()

    #
    # Discord.py events
    #
    async def on_ready(self):
        print("Logged on as", self.user)

        # Make sure our channel id is valid
        channel = self.get_channel(self.channel_id)
        if not channel:
            logger.warning("Invalid channel id %s!" % self.channel_id)
            channel = next(self.get_all_channels())
            self.channel_id = channel.id
            logger.warning("Using channel %s instead!" % channel.name)

        # Find the last time we said something
        limit = 6

        try:
            self.cur_msg = await channel.fetch_message(
                self.persistent_msg_id)
        except discord.errors.NotFound:
            pass

        if self.cur_msg:
            print("Found last message", self.cur_msg.id)

        async for msg in channel.history(limit=limit):
            if self.cur_msg and msg.id == self.cur_msg.id:
                break
            self.num_other_msgs += 1
            # We didn't find anything, just print a new list
            if self.num_other_msgs >= limit:
                await self.print_list()
                break

    async def on_message(self, message):
        # Not cached yet.
        if not self.is_ready():
            return
        # Listen for commands in our channel only.
        if message.channel.id != self.channel_id:
            return
        # This is our message, ignore it.
        if self.cur_msg and message.id == self.cur_msg.id:
            return

        self.num_other_msgs += 1

        if not message.content or message.content[0] != '!':
            return
        if not self.should_query() and not self.should_print_new_msg():
            return
        if message.content[1:] in ('servers', 'serverlist', 'list'):
            await self.print_list()

    async def on_message_delete(self, message):
        # Not cached yet.
        if not self.is_ready():
            return
        if not self.cur_msg:
            return
        if self.cur_msg.id == message.id:
            self.cur_msg = None  # Our message, clear cache
        if message.channel.id == self.channel_id:
            self.num_other_msgs -= 1

    @tasks.loop(seconds=3)
    async def update_task(self):
        """The update loop where we query servers."""
        if self.should_query():
            await self.print_list()

    @update_task.before_loop
    async def before_update_task(self):
        # Wait until we're ready.
        await self.wait_until_ready()

    #
    # Our stuff
    #
    async def query_newlist(self):
        """Returns the server list depending on the configuration options."""
        self.num_offline = 0

        new_lst = None

        if self.user_serverlist:
            # User wants a specific list from ips.
            new_lst = await self.loop.run_in_executor(
                None,
                self.query_servers, self.user_serverlist)
        elif self.should_query_last_list():
            # Query the servers we've already collected.
            addresses = self.serverlist.get_addresses()
            new_lst = await self.loop.run_in_executor(
                None,
                self.query_servers, addresses)
        else:
            # Just query masterserver.
            addresses = await self.query_masterserver(
                None if not self.config.gamedir else self.config.gamedir)
            new_lst = await self.loop.run_in_executor(
                None,
                self.query_servers, addresses)

        self.last_query_time = time.time()

        return new_lst

    async def query_masterserver(self, gamedir):
        """Queries the Source master server list and returns all
        addresses found.
        Should keep these queries to the minimum,
        or you get timed out."""
        logger.info("Querying masterserver...")

        # TODO: More options?
        ret = []

        try:
            max_total_query_time = self.config.max_total_query_time
            query_start = time.time()

            for address in steam.game_servers.query_master("\\gamedir\\" + gamedir):
                if self.is_blacklisted(address):
                    continue

                ret.append(address)

                if (time.time() - query_start) > max_total_query_time:
                    break
        except (OSError, ConnectionError, RuntimeError) as e:
            logger.error(
                "Connection error querying master server: " + str(e))
        self.last_ms_query_time = time.time()

        return ret

    def query_servers(self, addresses):
        logger.info("Querying %i servers..." % (len(addresses)))

        srv_lst = ServerList()
        query_start = time.time()

        for address in addresses:
            info = self.query_server_info(address)
            if info:
                srv = ServerData(address)
                srv.update_info(info)
                srv_lst.add_server(srv)

            if (time.time() - query_start) > self.config.max_total_query_time:
                break

        srv_lst.query_time = time.time()

        return srv_lst

    def query_server_info(self, address):
        # logger.info("Querying server %s..." % (address_to_str(address)))

        try:
            info = a2s.info(address)
            return info
        except socket.timeout:
            logger.info(
                "Couldn't contact server %s!" % address_to_str(address))
            self.num_offline += 1
        except (a2s.BrokenMessageError,
                a2s.BufferExhaustedError,
                socket.gaierror,
                ConnectionError,
                OSError) as e:
            logger.error(
                "Connection error querying server: %s" % (e))
            self.num_offline += 1

        return None

    async def get_serverlist(self):
        if self.should_query():
            new_lst = await self.query_newlist()
            self.serverlist.update(
                new_lst, self.config.max_unresponsive_time)
        return self.serverlist

    @staticmethod
    def parse_ips(ip_list):
        lst = []

        for address in ip_list.split(','):
            ip = address.split(':')
            ip[0] = ip[0].strip()

            if not ip[0]:
                continue

            ip_port = 0 if len(ip) <= 1 else int(ip[1])

            logger.debug("Parsed ip %s (%s)!" % (ip[0], ip_port))
            lst.append((ip[0], ip_port))

        return lst

    def is_blacklisted(self, address):
        for blacklisted in self.user_blacklist:
            if address_equals(blacklisted, address):
                return True
        return False

    def should_query(self):
        # We haven't even queried yet
        if len(self.serverlist.servers) < 1:
            return True

        time_delta = time.time() - self.last_query_time
        if time_delta > self.config.server_query_interval:
            return True
        else:
            return False

    async def print_list(self, lst=None):
        if not lst:
            lst = await self.get_serverlist()

            if not lst:
                logger.info("Nothing to print!")
                return

        if self.should_print_new_msg():
            await self.send_newlist(lst)
        else:
            await self.send_editlist(lst)

    def should_print_new_msg(self):
        if self.cur_msg is None:
            return True

        # Too many messages to see it
        if self.num_other_msgs > self.config.max_new_msgs:
            return True

        return False

    def should_query_last_list(self):
        if len(self.serverlist.servers) < 1:
            return False

        time_delta = time.time() - self.last_ms_query_time
        return True if time_delta < self.config.query_interval else False

    def build_serverlist_embed(self, lst):
        # Sort according to player count
        servers = sorted(
            lst.servers,
            key=lambda srv: srv.ply_count,
            reverse=True)
        # I just had a deja vu...
        # ABOUT THIS EXACT CODE AND ME EXPLAINING IT IN THIS COMMENT
        # FREE WILL IS A LIE
        # WE LIVE IN A SIMULATION
        description = "%i server(s) online" % (len(servers))

        if self.num_offline > 0:
            description += ", %i offline" % self.num_offline

        description += ("\nUpdating every %i seconds" %
                        (self.config.server_query_interval))

        em = discord.Embed(
            title=self.config.embed_title,
            description=description,
            colour=self.config.embed_color)
        counter = 0
        for srv in servers:
            ply_count = srv.ply_count
            max_players = srv.max_ply_count
            srv_name = srv.server_name
            srv_map = srv.map_name
            srv_adrss = srv.full_socket

            em.add_field(
                name=f"{ply_count}/{max_players} | {srv_name}",
                value=f"Map: {srv_map} | Connect: steam://connect/{srv_adrss}",
                inline=False)

            counter += 1
            if counter >= self.config.embed_max:
                break

        return em

    async def send_newlist(self, lst):
        channel = self.get_channel(self.channel_id)

        self.num_other_msgs = 0
        curtime = time.time()

        # Remove old message.
        await self.remove_oldlist()

        try:
            embed = self.build_serverlist_embed(lst)
            self.cur_msg = await channel.send(embed=embed)
            self.last_print_time = self.last_action_time = curtime
            logger.info("Printed new list.")

            # Make sure we remember this message.
            if self.cur_msg.id != self.persistent_msg_id:
                self.write_persistent_last_msg()
        except (discord.HTTPException,
                discord.Forbidden,
                discord.InvalidArgument) as e:
            logger.error(
                "Failed to print new list. Exception: %s" % (e))

    async def send_editlist(self, lst):
        curtime = time.time()

        try:
            embed = self.build_serverlist_embed(lst)
            await self.cur_msg.edit(embed=embed)
            self.last_action_time = curtime
            logger.info("Edited existing list.")
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.error(
                "Failed to edit existing list. Exception: %s" % (e))

    async def remove_oldlist(self):
        try:
            if self.cur_msg:
                await self.cur_msg.delete()
                self.cur_msg = None
                logger.info("Removed old list.")
        except (discord.HTTPException,
                discord.NotFound,
                discord.Forbidden) as e:
            logger.error(
                "Failed to remove old list. Exception: %s" % (e))

    @staticmethod
    def get_persistent_last_msg_name():
        return path.join(
            path.dirname(__file__), ".persistent_lastmsg.txt")

    def read_persistent_last_msg(self):
        file_name = self.get_persistent_last_msg_name()
        try:
            with open(file_name, "r") as fp:
                self.persistent_msg_id = int(fp.read())
        except IOError:
            pass

    def write_persistent_last_msg(self):
        file_name = self.get_persistent_last_msg_name()
        with open(file_name, "w") as fp:
            fp.write(str(self.cur_msg.id) + "\n")
        self.persistent_msg_id = self.cur_msg.id


if __name__ == "__main__":
    # Our running script will use the exit code
    # to determine whether to stop the execution loop or not.
    exitcode = 0

    # Read our config
    config = configparser.ConfigParser()
    config_name = path.join(
        path.dirname(__file__), ".ssdb_config.ini")
    with open(config_name, 'r') as fp:
        config.read_file(fp)

    # Init logger
    log_level_str = config.get(
        'config', 'logging', fallback='').upper()
    log_level = getattr(logging, log_level_str, logging.WARNING)
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    formatter = logging.Formatter(LOG_FORMAT)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Run the bot
    client = ServerListClient(config)
    try:
        client.run(config.get('config', 'token'))
    except discord.LoginFailure:
        logger.error("Failed to log in! Make sure your token is correct!")
        exitcode = 1
    except Exception as e:
        logger.error("Discord bot ended unexpectedly: " + str(e))

    if exitcode > 0:
        sys.exit(exitcode)
