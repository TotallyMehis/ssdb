import time
import datetime
import configparser
import sys
from os import path

import discord
import asyncio

import valve.source
import valve.source.a2s
import valve.source.master_server


DATE_FORMAT = '%Y/%m/%d %H:%M:%S'


def value_cap_min(value, min=0.0, def_value=30.0):
    if value > min:
        return value
    else:
        return def_value


def safe_cast(value, to_type=int, def_value=0, base=0):
    try:
        if base != 0:
            return to_type(value, base)
        return to_type(value)
    except (ValueError, TypeError):
        return def_value


def read_config_safe(config, name, def_value):
    try:
        return config.get('config', name)
    except (configparser.Error):
        pass
    return def_value


class ServerListConfig:
    def __init__(self, config):
        self.embed_title = config.get('config', 'embed_title')
        self.embed_max = safe_cast(config.get('config', 'embed_max'))
        self.embed_max = 1 if self.embed_max < 1 else self.embed_max
        self.embed_color = safe_cast(
            config.get('config', 'embed_color'),
            base=16)
        self.gamedir = config.get('config', 'gamedir')
        self.max_total_query_time = float(
            config.get('config', 'max_total_query_time'))
        self.max_total_query_time = value_cap_min(
            self.max_total_query_time)
        self.query_interval = float(config.get('config', 'query_interval'))
        self.query_interval = value_cap_min(
            self.query_interval)
        self.server_query_interval = float(
            config.get('config', 'server_query_interval'))
        self.server_query_interval = value_cap_min(
            self.server_query_interval)

        self.max_new_msgs = read_config_safe(config, 'max_new_msgs', '5')
        self.max_new_msgs = safe_cast(self.max_new_msgs)


class ServerListClient(discord.Client):
    """Task: Prints an embed list of servers.
    Responds to commands (!serverlist/!servers) whenever possible."""
    def __init__(self, config):
        super().__init__()
        # The Channel ID we will use
        self.channel_id = safe_cast(config.get('config', 'channel'))
        self.config = ServerListConfig(config)
        self.user_serverlist = self.parse_ips(
            config.get('config', 'serverlist'))
        self.user_blacklist = self.parse_ips(
            config.get('config', 'blacklist'))

        self.last_serverlist = []
        self.last_action_time = 0.0  # Last time we edited or printed a message
        self.last_print_time = 0.0
        self.last_query_time = 0.0
        self.last_ms_query_time = 0.0
        self.num_offline = 0  # Number of servers we couldn't contact
        self.cur_msg = None  # The message we should edit
        self.persistent_msg_id = 0
        self.num_other_msgs = 0  # How many messages between our msg and now

        self.read_persistent_last_msg()

        self.loop.create_task(self.update_loop())

    #
    # Discord.py events
    #
    async def on_ready(self):
        print("Logged on as", self.user)

        # Make sure our channel id is valid
        channel = self.get_channel(self.channel_id)
        if not channel:
            print("Invalid channel id %s!" % self.channel_id)
            channel = next(self.get_all_channels())
            self.channel_id = channel.id
            print("Using channel %s instead!" % channel.name)

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
                await self.print_list(await self.get_serverlist())
                break

    async def on_message(self, message):
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
            await self.print_list(await self.get_serverlist())

    async def on_message_delete(self, message):
        if not self.cur_msg:
            return
        if self.cur_msg.id == message.id:
            self.cur_msg = None  # Our message, clear cache
        if message.channel.id == self.channel_id:
            self.num_other_msgs -= 1

    #
    # Our stuff
    #
    """The update loop where we query servers."""
    async def update_loop(self):
        # Query servers on an interval
        await self.wait_until_ready()

        # Wait a bit before starting
        await asyncio.sleep(self.config.server_query_interval)

        while not self.is_closed():
            if self.should_query():
                new_list = await self.query_newlist()
                if self.list_differs(new_list):
                    print("List differs! Updating...")
                    await self.print_list(new_list)
                await asyncio.sleep(self.get_sleeptime())
            else:
                await asyncio.sleep(3)

        print("Update loop ending...")

    """Returns the server list depending on the configuration options."""
    async def query_newlist(self):
        self.num_offline = 0
        serverlist = []
        if self.user_serverlist:
            # User wants a specific list from ips.
            serverlist = await self.query_servers(self.user_serverlist)
        elif self.should_query_last_list():
            # Query the servers we've already collected.
            lastservers = []
            for info in self.last_serverlist:
                lastservers.append(info['address_real'])
            serverlist = await self.query_servers(lastservers)
        else:
            # Just query master server.
            serverlist = await self.query_masterserver(
                None if not self.config.gamedir else self.config.gamedir)

        self.last_query_time = time.time()

        return serverlist

    """Queries the Source master server list and return them.
    Should keep these queries to the minimum, or you get timed out."""
    async def query_masterserver(self, gamedir):
        # TODO: More options?
        ret = []
        with valve.source.master_server.MasterServerQuerier() as msq:
            try:
                max_total_query_time = self.config.max_total_query_time
                query_start = time.time()

                for address in msq.find(gamedir=gamedir):
                    if self.is_blacklisted(address):
                        continue
                    info = await self.query_server_info(address)
                    if info:
                        ret.append(info)
                    if (time.time() - query_start) > max_total_query_time:
                        break
            except valve.source.NoResponseError:
                self.log_activity(
                    time.time(),
                    "Master server request timed out!")
            except OSError as e:
                self.log_activity(
                    time.time(),
                    "OSError when querying master server: " + str(e))
            self.last_ms_query_time = time.time()
        return ret

    async def query_servers(self, whitelist):
        ret = []
        query_start = time.time()

        for address in whitelist:
            info = await self.query_server_info(address)
            if info:
                ret.append(info)
            if (time.time() - query_start) > self.config.max_total_query_time:
                break
        return ret

    async def query_server_info(self, address):
        try:
            with valve.source.a2s.ServerQuerier(address) as server:
                if not server:
                    return None
                # Copy the server info
                info = server.info()
                # Ignore bots if possible.
                new_count = info['player_count'] - info['bot_count']
                info['player_count'] = (
                    new_count if new_count >= 0 else info['player_count'])
                info['address_real'] = address
                info['address'] = "%s:%i" % (address[0], address[1])
                return info
        except Exception:
            self.log_activity(
                time.time(),
                "Couldn't contact server %s!" % self.address_to_str(address))
            self.num_offline += 1
            return None

    async def get_serverlist(self):
        if self.should_query():
            return await self.query_newlist()
        return self.last_serverlist

    @staticmethod
    def parse_ips(ip_list):
        lst = []

        for address in ip_list.split(','):
            ip = address.split(':')
            ip[0] = ip[0].strip()

            if not ip[0]:
                continue

            ip_port = 0 if len(ip) <= 1 else int(ip[1])

            print("Parsed ip %s (%s)!" % (ip[0], ip_port))
            lst.append([ip[0], ip_port])

        return lst

    def is_blacklisted(self, address):
        for blacklisted in self.user_blacklist:
            if self.address_equals(blacklisted, address):
                return True
        return False

    @staticmethod
    def address_to_str(address):
        if address[1] == 0:
            return address[0]
        else:
            return ("%s:%i" % (address[0], address[1]))

    @staticmethod
    def address_equals(a1, a2):
        if a1[0] == a2[0]:
            # If port is 0, ignore it
            if a1[1] == 0 or a2[1] == 0:
                return True
            elif a1[1] == a2[1]:
                return True
        return False

    def list_differs(self, new_list):
        if not self.last_serverlist:
            return True

        if len(new_list) != len(self.last_serverlist):
            return True

        for (nServer, oServer) in zip(new_list, self.last_serverlist):
            if nServer['player_count'] != oServer['player_count']:
                return True
            if nServer['server_name'] != oServer['server_name']:
                return True
            if nServer['map'] != oServer['map']:
                return True
        return False

    def should_query(self):
        # We haven't even queried yet
        if not self.last_serverlist:
            return True

        time_delta = time.time() - self.last_query_time
        if time_delta > self.config.server_query_interval:
            return True
        else:
            return False

    def get_sleeptime(self):
        queryinterval = self.config.server_query_interval
        time_delta = time.time() - self.last_query_time
        to_sleep = queryinterval - time_delta
        min_sleep_time = 5.0

        return to_sleep if to_sleep > min_sleep_time else min_sleep_time

    @staticmethod
    def get_datetime(timestamp):
        return datetime.datetime.fromtimestamp(timestamp).strftime(DATE_FORMAT)

    def log_activity(self, time, msg):
        print(self.get_datetime(time) + " | " + msg)

    async def print_list(self, l):
        if self.should_print_new_msg():
            channel = self.get_channel(self.channel_id)
            await self.send_newlist(channel, l)
        else:
            await self.send_editlist(l)

        self.last_serverlist = l

    def should_print_new_msg(self):
        if self.cur_msg is None:
            return True

        # Too many messages to see it
        if self.num_other_msgs > self.config.max_new_msgs:
            return True

        return False

    def should_query_last_list(self):
        if not self.last_serverlist:
            return False

        time_delta = time.time() - self.last_ms_query_time
        return True if time_delta < self.config.query_interval else False

    def build_serverlist_embed(self, l):
        # Sort according to player count
        serverlist = sorted(
            l,
            key=lambda item: item['player_count'],
            reverse=True)
        # I just had a deja vu...
        # ABOUT THIS EXACT CODE AND ME EXPLAINING IT IN THIS COMMENT
        # FREE WILL IS A LIE
        # WE LIVE IN A SIMULATION
        description = "%i server(s) online" % len(l)

        if self.num_offline > 0:
            description += ", %i offline" % self.num_offline

        em = discord.Embed(
            title=self.config.embed_title,
            description=description,
            colour=self.config.embed_color)
        counter = 0
        for info in serverlist:
            ply_count = info['player_count']
            max_players = info['max_players']
            srv_name = info['server_name']
            srv_map = info['map']
            srv_adrss = info['address']

            em.add_field(
                name=f"{ply_count}/{max_players} | {srv_name}",
                value=f"Map: {srv_map} | Connect: steam://connect/{srv_adrss}",
                inline=False)

            counter += 1
            if counter >= self.config.embed_max:
                break

        return em

    async def send_newlist(self, channel, l):
        self.num_other_msgs = 0
        curtime = time.time()

        try:
            self.cur_msg = await channel.send(
                embed=self.build_serverlist_embed(l))
            self.last_print_time = self.last_action_time = curtime
            self.log_activity(self.last_action_time, "Printed new list.")

            # Make sure we remember this message.
            if self.cur_msg.id != self.persistent_msg_id:
                self.write_persistent_last_msg()
        except Exception:
            self.log_activity(
                curtime,
                "Failed to print new list. Exception: " + str(e))

    async def send_editlist(self, l):
        curtime = time.time()

        try:
            await self.cur_msg.edit(embed=self.build_serverlist_embed(l))
            self.last_action_time = curtime
            self.log_activity(self.last_action_time, "Edited existing list.")
        except Exception as e:
            self.log_activity(
                curtime,
                "Failed to edit existing list. Exception: " + str(e))

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

    # Run the bot
    client = ServerListClient(config)
    try:
        client.run(config.get('config', 'token'))
    except discord.LoginFailure:
        print("Failed to log in! Make sure your token is correct!")
        exitcode = 1
    except Exception as e:
        print("Discord bot ended unexpectedly: " + str(e))

    if exitcode > 0:
        sys.exit(exitcode)
