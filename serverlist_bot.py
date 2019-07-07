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


class Bot:
    """Holds tasks and passes events to them"""
    def __init__( self ):
        print( 'Starting bot...' )
        self.client = discord.Client()

    def run( self, token, tasks ):
        self.tasks = tasks
        for task in self.tasks:
            task.create_task()
        self.client.run( token )
        
    def end( self ):
        for task in self.tasks:
            task.free_task()
        
    async def on_ready( self ):
        for task in self.tasks:
            await task.on_ready()
    
    async def on_message( self, message ):
        for task in self.tasks:
            await task.on_message( message )
            
    async def on_message_delete( self, message ):
        for task in self.tasks:
            await task.on_message_delete( message )
    

# Create the bot that will handle tasks
BOT = Bot()

@BOT.client.event
async def on_ready():
    print( "Logged in as " + BOT.client.user.name )
    await BOT.on_ready()
    
@BOT.client.event
async def on_message( message ):
    await BOT.on_message( message )
    
@BOT.client.event
async def on_message_delete( message ):
    await BOT.on_message_delete( message )
    
    
    
class BaseTask:
    def __init__( self ):
        pass
        
    def create_task( self ):
        pass
    
    def free_task( self ):
        pass
        
    async def on_ready( self ):
        pass
        
    async def on_message( self, message ):
        pass
        
    async def on_message_delete( self, message ):
        pass
    
class ServerListConfig:
    def __init__( self, config ):
        self.embed_title = config.get( 'config', 'embed_title' )
        self.embed_max = int( config.get( 'config', 'embed_max' ) )
        self.embed_max = 1 if self.embed_max < 1 else self.embed_max
        self.embed_color = int( config.get( 'config', 'embed_color' ), 16 )
        self.gamedir = config.get( 'config', 'gamedir' )
        self.max_total_query_time = float( config.get( 'config', 'max_total_query_time' ) )
        self.max_total_query_time = self.max_total_query_time if self.max_total_query_time > 0.0 else 30.0
        self.query_interval = float( config.get( 'config', 'query_interval' ) )
        self.query_interval = self.query_interval if self.query_interval > 0.0 else 30.0
        self.server_query_interval = float( config.get( 'config', 'server_query_interval' ) )
        self.server_query_interval = self.server_query_interval if self.server_query_interval > 0.0 else 30.0
    
class ServerList(BaseTask):
    """Task: Prints an embed list of servers. Responds to commands (!serverlist/!servers) whenever possible."""
    def __init__( self, config ):
        BaseTask.__init__( self )
        self.channel_id = config.get( 'config', 'channel' ) # The Channel ID we will use
        self.config = ServerListConfig( config )
        self.user_serverlist = self.parse_ips( config.get( 'config', 'serverlist' ) )
        self.user_blacklist = self.parse_ips( config.get( 'config', 'blacklist' ) )
        self.last_serverlist = []
        self.last_action_time = 0.0 # Last time we edited or printed a message
        self.last_print_time = 0.0
        self.last_query_time = 0.0
        self.last_ms_query_time = 0.0
        self.num_offline = 0 # Number of servers we couldn't contact
        self.cur_msg = None # The message we should edit
        self.num_other_msgs = 0 # How many messages between our msg and now
        
    def create_task( self ):
        BOT.client.loop.create_task( self.update_loop() )
        
    async def on_ready( self ):
        # Make sure our channel id is valid
        channel = BOT.client.get_channel( self.channel_id )
        if not channel:
            print( "Invalid channel id %s!" % self.channel_id )
            channel = next( BOT.client.get_all_channels() )
            self.channel_id = channel.id
            print( "Using channel %s instead!" % channel.name )
        limit = 6
        # Find the last time we said something
        async for msg in BOT.client.logs_from( channel, limit = limit ):
            if msg.author.id == BOT.client.user.id:
                self.cur_msg = msg
                await self.print_list( await self.get_serverlist() )
                break
            self.num_other_msgs += 1
            # We didn't find anything, just print a new list
            if self.num_other_msgs >= limit:
                await self.print_list( await self.get_serverlist() )
                break
        
    async def on_message( self, message ):
        # Listen for commands in our channel.
        if message.channel.id != self.channel_id:
            return
        if message.author.id == BOT.client.user.id:
            return
        self.num_other_msgs += 1
        if message.content[0] != '!':
            return
        if not self.should_query() and not self.should_print_new_msg():
            return
        if message.content[1:] in ( 'servers', 'serverlist', 'list' ):
            await self.print_list( await self.get_serverlist() )
        
    async def on_message_delete( self, message ):
        if self.cur_msg.id == message.id:
            self.cur_msg = None # Our message, clear cache
        if self.cur_msg and message.channel.id == self.channel_id:
            self.num_other_msgs -= 1
        
    async def update_loop( self ):
        # Query servers on an interval
        await BOT.client.wait_until_ready()
        await asyncio.sleep( self.config.server_query_interval ) # Wait a bit before starting
        while not BOT.client.is_closed:
            if self.should_query():
                new_list = await self.query_newlist()
                if self.list_differs( new_list ):
                    print( "List differs! Updating..." )
                    await self.print_list( new_list )
                await asyncio.sleep( self.get_sleeptime() )
            else:
                await asyncio.sleep( 3 )
        print( "Update loop ending..." )
        
    async def query_newlist( self ):
        # Return the server list depending on option
        self.num_offline = 0
        serverlist = []
        if self.user_serverlist:
            # User wants a specific list from ips.
            serverlist = await self.query_servers( self.user_serverlist )
        elif self.should_query_last_list():
            # Query the servers we've already collected.
            lastservers = []
            for info in self.last_serverlist:
                lastservers.append( info['address_real'] )
            serverlist = await self.query_servers( lastservers )
        else:
            # Just query master server.
            serverlist = await self.query_masterserver( None if not self.config.gamedir else self.config.gamedir )
        self.last_query_time = time.time()
        return serverlist
    
    async def query_masterserver( self, gamedir ):
        # TODO: More options?
        ret = []
        with valve.source.master_server.MasterServerQuerier() as msq:
            try:
                query_start = time.time()
                for address in msq.find( gamedir = gamedir ):
                    if self.is_blacklisted( address ):
                        continue
                    info = await self.query_server_info( address )
                    if info:
                        ret.append( info )
                    if (time.time() - query_start) > self.config.max_total_query_time:
                        break
            except valve.source.NoResponseError:
                self.log_activity( time.time(), "Master server request timed out!" )
            self.last_ms_query_time = time.time()
        return ret
        
    async def query_servers( self, list ):
        ret = []
        query_start = time.time()
        for address in list:
            info = await self.query_server_info( address )
            if info:
                ret.append( info )
            if (time.time() - query_start) > self.config.max_total_query_time:
                break
        return ret
    
    async def query_server_info( self, address ):
        try:
            with valve.source.a2s.ServerQuerier( address ) as server:
                if not server:
                    return None
                # Copy the server info
                info = server.info()
                # Ignore bots if possible.
                new_count = info['player_count'] - info['bot_count']
                info['player_count'] = new_count if new_count >= 0 else info['player_count']
                info['address_real'] = address
                info['address'] = "%s:%i" % ( address[0], address[1] )
                return info
        except:
            self.log_activity( time.time(), "Couldn't contact server %s!" % self.address_to_str( address ) )
            self.num_offline += 1
            return None
    
    async def get_serverlist( self ):
        if self.should_query():
            return await self.query_newlist()
        return self.last_serverlist
        
    @staticmethod
    def parse_ips( ip_list ):
        list = []
        for address in ip_list.split( ',', 2 ):
            ip = address.split( ':' )
            ip[0] = ip[0].strip()
            if not ip[0]:
                continue
            print( "Parsed ip %s!" % ip[0] )
            list.append( [ ip[0], 0 if len( ip ) <= 1 else int( ip[1] ) ] )
        return list
        
    def is_blacklisted( self, address ):
        for blacklisted in self.user_blacklist:
            if self.address_equals( blacklisted, address ):
                return True
        return False
    
    @staticmethod
    def address_to_str( address ):
        return address[0] if address[1] == 0 else ("%s:%i" % (address[0], address[1]) )
    
    @staticmethod
    def address_equals( a1, a2 ):
        if a1[0] == a2[0]:
            # If port is 0, ignore it
            if a1[1] == 0 or a2[1] == 0:
                return True
            elif a1[1] == a2[1]:
                return True
        return False
        
    def list_differs( self, newList ):
        if not self.last_serverlist:
            return True
        if len( newList ) != len( self.last_serverlist ):
            return True
        for ( nServer, oServer ) in zip( newList, self.last_serverlist ):
            if nServer['player_count'] != oServer['player_count']:
                return True
            if nServer['server_name'] != oServer['server_name']:
                return True
            if nServer['map'] != oServer['map']:
                return True
        return False

    def should_query( self ):
        if not self.last_serverlist: # We haven't even queried yet
            return True
        time_delta = time.time() - self.last_query_time
        return True if time_delta > self.config.server_query_interval else False
    
    def get_sleeptime( self ):
        queryinterval = self.config.server_query_interval
        time_delta = time.time() - self.last_query_time
        to_sleep = queryinterval - time_delta
        min_sleep_time = 5.0
        return to_sleep if to_sleep > min_sleep_time else min_sleep_time
    
    @staticmethod
    def get_datetime( timestamp ):
        return datetime.datetime.fromtimestamp( timestamp ).strftime( DATE_FORMAT )
    
    def log_activity( self, time, msg ):
        print( self.get_datetime( time ) + " | " + msg )
    
    async def print_list( self, list ):
        if self.should_print_new_msg():
            channel = BOT.client.get_channel( self.channel_id )
            await self.send_newlist( channel, list )
        else:
            await self.send_editlist( list )
        self.last_serverlist = list
        
    def should_print_new_msg( self ):
        if self.cur_msg is None:
            return True
        time_delta = time.time() - self.last_print_time
        if self.num_other_msgs > 3 and time_delta > 1800.0: # It has been a while, just make a new one
            return True
        if self.num_other_msgs > 8: # Too many messages to see it
            return True
        return False

    def should_query_last_list( self ):
        if not self.last_serverlist:
            return False
        time_delta = time.time() - self.last_ms_query_time
        return True if time_delta < self.config.query_interval else False

    def build_serverlist_embed( self, list ):
        # Sort according to player count
        serverlist = sorted( list, key=lambda item: item['player_count'], reverse = True )
        # I just had a deja vu... ABOUT THIS EXACT CODE AND ME EXPLAINING IT IN THIS COMMENT
        # FREE WILL IS A LIE
        # WE LIVE IN A SIMULATION
        description = "%i server(s) online" % len( list )
        if self.num_offline > 0:
            description += ", %i offline" % self.num_offline
        em = discord.Embed(
            title = self.config.embed_title,
            description = description,
            colour = self.config.embed_color )
        counter = 0
        for info in serverlist:
            em.add_field(
                name = "{player_count}/{max_players} | {server_name}".format( **info ),
                value = "Map: {map} | Connect: steam://connect/{address}".format( **info ),
                inline = False )
            counter += 1
            if counter >= self.config.embed_max:
                break
        return em
    
    async def send_newlist( self, channel, list ):
        self.num_other_msgs = 0
        curtime = time.time()
        try:
            self.cur_msg = await BOT.client.send_message( channel, embed = self.build_serverlist_embed( list ) )
            self.last_print_time = self.last_action_time = curtime
            self.log_activity( self.last_action_time, "Printed new list." )
        except:
            self.log_activity( curtime, "Failed to print new list." )
        
    async def send_editlist( self, list ):
        curtime = time.time()
        try:
            await BOT.client.edit_message( self.cur_msg, embed = self.build_serverlist_embed( list ) )
            self.last_action_time = curtime
            self.log_activity( self.last_action_time, "Edited existing list." )
        except:
            self.log_activity( curtime, "Failed to edit existing list." )


if __name__ == "__main__":
    # Our running script will use the exit code to determine whether to stop the execution loop or not.
    exitcode = 0
    # Read our config
    config = configparser.ConfigParser()
    config.readfp( open( path.join( path.dirname( __file__ ), "serverlist_config.ini" ) ) )
    # Run the bot
    try:
        BOT.run( config.get( 'config', 'token' ),
            [
            ServerList( config )
            # Add other tasks here
            ] )
    except discord.LoginFailure:
        print( "Failed to log in! Make sure your token is correct!" )
        exitcode = 1
    except Exception as e:
        print( "Discord bot ended unexpectedly: " + str( e ) )
    BOT.end()
    if exitcode > 0:
        sys.exit( exitcode )
