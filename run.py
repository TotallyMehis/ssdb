"""Source Servers Discord Bot"""
import configparser
import sys
from os import path
import logging
import discord
from discord.ext import tasks
from ssdb import ServerList, SSDBConfig, _build_list_embed, parse_config, \
    read_persisted_msg_id, write_persistent_msg_id, QuerySystem

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"

logger = logging.getLogger(__name__)


class SSDBClient(discord.Client):
    """Prints an embed with a list of game servers."""

    def __init__(self, config: SSDBConfig):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)

        self._init_done = False
        self._config = config
        self._cur_msg: discord.Message | None = None  # The message we should edit
        self._num_other_msgs = 0  # How many messages between our msg and now
        self._persistent_msg_id = read_persisted_msg_id()
        self._query_system = QuerySystem(appid=self._config.appid,
                                         gamedir=self._config.gamedir,
                                         limit=self._config.limit,
                                         webapi_key=self._config.steam_webapi_key,
                                         whitelist=self._config.whitelist,
                                         blacklist=self._config.blacklist,
                                         max_ms_query_time=self._config.max_total_query_time,
                                         max_total_query_time=self._config.max_total_query_time,
                                         max_unresponsive_time=self._config.max_unresponsive_time,
                                         ms_query_interval=self._config.query_interval)

    async def setup_hook(self):
        self.update_task.start()

    async def on_ready(self):
        """Init"""
        logger.info("Logged on as %s", self.user)

        # Make sure our channel id is valid
        channel = self.get_channel(self._config.channel_id)
        if not channel:
            logger.error("Invalid channel id %d!", self._config.channel_id)
            sys.exit(1)

        # Find the last time we said something
        if self._persistent_msg_id:
            try:
                self._cur_msg = await channel.fetch_message(
                    self._persistent_msg_id)
            except discord.NotFound:
                logger.debug(
                    "Could not find persistent message by id %d.", self._persistent_msg_id)
            except discord.DiscordException as e:
                logger.error(
                    "Failed to fetch message persistent last message. Exception: %s", e)

            if self._cur_msg:
                logger.info("Found last message %d", self._cur_msg.id)

            limit = self._config.max_new_msgs or 6
            async for msg in channel.history(limit=limit):
                if self._cur_msg and msg.id == self._cur_msg.id:
                    break
                self._num_other_msgs += 1
        self._init_done = True

    async def on_message(self, message: discord.Message):
        """Count messages in my channel."""
        if not self.is_ready():
            return
        if not self._init_done:
            logger.debug("Can't react to message, initializing not done yet.")
            return
        if message.channel.id != self._config.channel_id:
            return
        if self._cur_msg and message.id == self._cur_msg.id:
            return

        self._num_other_msgs += 1
        logger.debug(
            "New message. %d messages after our list.", self._num_other_msgs)

    async def on_message_delete(self, message: discord.Message):
        """Count messages in my channel."""
        if not self.is_ready():
            return
        if not self._init_done:
            return
        if not self._cur_msg:
            return
        if self._cur_msg.id == message.id:
            self._cur_msg = None  # Our message, clear cache
            logger.debug("Our message was removed.")
        if message.channel.id == self._config.channel_id and message.id > self._cur_msg.id:
            self._num_other_msgs -= 1
            if self._num_other_msgs < 0:
                self._num_other_msgs = 0
            logger.debug(
                "Removed message. %d messages after our list.", self._num_other_msgs)

    @tasks.loop(seconds=3)
    async def update_task(self):
        """The update loop where we update the list."""
        if not self._init_done:
            logger.debug(
                "Can't update list because initializing not done yet.")
            return
        updated = await self._query_system.update(self.loop)
        if updated:
            await self._print_list()

    @update_task.before_loop
    async def before_update_task(self):
        """Wait until we're ready."""
        await self.wait_until_ready()

    async def _print_list(self):
        lst = self._query_system.server_list
        if self._should_print_new_msg():
            await self._new_list(lst)
        else:
            await self._edit_list(lst)

    def _should_print_new_msg(self):
        if self._cur_msg is None:
            return True

        # Too many messages to see it
        if self._num_other_msgs > self._config.max_new_msgs:
            return True

        return False

    async def _new_list(self, lst: ServerList):
        channel = self.get_channel(self._config.channel_id)

        self._num_other_msgs = 0

        # Remove old message.
        await self._remove_old_list()

        embed = _build_list_embed(
            lst, self._config, self._query_system.num_offline)
        try:
            self._cur_msg = await channel.send(embed=embed)
            logger.info("Printed new list.")

            # Make sure we remember this message.
            if self._cur_msg.id != self._persistent_msg_id:
                write_persistent_msg_id(self._cur_msg.id)
                self._persistent_msg_id = int(self._cur_msg.id)
        except discord.DiscordException as e:
            logger.error(
                "Failed to print new list. Exception: %s", e)

    async def _edit_list(self, lst: ServerList):
        assert self._cur_msg

        embed = _build_list_embed(
            lst, self._config, self._query_system.num_offline)
        try:
            await self._cur_msg.edit(embed=embed)
            logger.info("Edited existing list.")
        except discord.DiscordException as e:
            logger.error(
                "Failed to edit existing list. Exception: %s", e)

    async def _remove_old_list(self):
        try:
            if self._cur_msg:
                await self._cur_msg.delete()
                self._cur_msg = None
                logger.info("Removed old list.")
        except discord.DiscordException as e:
            logger.error(
                "Failed to remove old list. Exception: %s", e)


def _main():
    # Read our config
    prsr = configparser.ConfigParser()
    with open(path.join(path.dirname(__file__), ".ssdb_config.ini"), "r", encoding="utf-8") as fp:
        prsr.read_file(fp)

    config = parse_config(prsr)

    # Init logger
    log_level = getattr(logging, config.log_level, logging.INFO)
    logging.basicConfig(level=log_level, format=LOG_FORMAT)

    # Run the bot
    client = SSDBClient(config)
    try:
        client.run(config.token, log_handler=None)
    except discord.LoginFailure as e:
        logger.error(
            "Failed to log in! Make sure your token is correct! Exception: %s", e)
        sys.exit(2)
    except:  # pylint: disable=W0702
        logger.error("Discord bot ended unexpectedly.", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    _main()
