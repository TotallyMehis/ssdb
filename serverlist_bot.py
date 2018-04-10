import time

import discord
import asyncio

import valve.source
import valve.source.a2s
import valve.source.master_server


GAME_DIR = u"zombie_master_reborn" # The game directory to look for
LIST_EMBED_MAX = 10 # Max amount of servers to print in the embed
LIST_EMBED_TITLE = "ZM Reborn Servers"
LIST_EMBED_COLOR = 0x681414

QUERY_INTERVAL = 300 # Query servers every this many seconds
# Allow queries every this many seconds (from user commands)
# You shouldn't change this to be too low or the query will take too long and the bot will disconnect.
MIN_QUERY_INTERVAL = 150


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
        
    async def on_ready( self ):
        pass
        
    async def on_message( self, message ):
        pass
        
    async def on_message_delete( self, message ):
        pass
    
class ZMServerList(BaseTask):
    """Task: Prints an embed list of ZM servers. Responds to commands (!serverlist/!servers) whenever possible."""
    def __init__( self, channel_id ):
        BaseTask.__init__( self )
        self.channel_id = channel_id # The Channel ID we will use
        self.last_serverlist = []
        self.last_action_time = 0.0
        self.last_print_time = 0.0
        self.last_query_time = 0.0
        self.cur_msg = None # The message we should edit
        self.num_other_msgs = 0 # How many messages between our msg and now
        
    def create_task( self ):
        BOT.client.loop.create_task( self.update_loop() )
        
    async def on_ready( self ):
        # Find the last time we said something
        channel = BOT.client.get_channel( self.channel_id )
        limit = 6
        async for msg in BOT.client.logs_from( channel, limit = limit ):
            # We already got it
            if self.last_action_time != 0:
                break
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
        if message.channel.id != self.channel_id:
            return
        if message.author.id == BOT.client.user.id:
            return
        self.num_other_msgs += 1
        if message.content[0] != '!': # Is comment?
            return
        if not self.should_query() and not self.should_print_new_msg():
            return
        if message.content[1:] in ( 'servers', 'serverlist', 'list' ):
            await self.print_list( await self.get_serverlist() )
        
    async def on_message_delete( self, message ):
        if self.cur_msg.id == message.id:
            self.cur_msg = None # Our message, clear cache
        if self.cur_msg is not None and message.channel.id == self.channel_id:
            self.num_other_msgs -= 1;
        
    async def update_loop( self ):
        await BOT.client.wait_until_ready()
        await asyncio.sleep( MIN_QUERY_INTERVAL ) # Wait a bit before starting
        while not BOT.client.is_closed:
            if self.should_query():
                new_list = await self.query_serverlist()
                if self.list_differs( new_list ):
                    print( "List differs! Updating..." )
                    await self.print_list( new_list )
            await asyncio.sleep( self.get_sleeptime() )
        
    async def query_serverlist( self ):
		# Doesn't utilize async :(
        self.last_query_time = time.time()
        serverlist = []
        with valve.source.master_server.MasterServerQuerier() as msq:
            try:
                for address in msq.find( gamedir = GAME_DIR ):
                    with valve.source.a2s.ServerQuerier( address ) as server:
                        # Copy the server info
                        info = server.info()
                        # Ignore bots if possible.
                        new_count = info['player_count'] - info['bot_count']
                        info['player_count'] = new_count if new_count >= 0 else info['player_count']
                        info['address_real'] = address
                        info['address'] = "%s:%i" % ( address[0], address[1] );
                        serverlist.append( info )
            except valve.source.NoResponseError:
                print( "Master server request timed out!" )
        return serverlist

    async def get_serverlist( self ):
        if self.should_query():
            return await self.query_serverlist()
        return self.last_serverlist
        
    def list_differs( self, newList ):
        if not self.last_serverlist:
            return False
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
        return True if time_delta > MIN_QUERY_INTERVAL else False
    
    def get_sleeptime( self ):
        time_delta = time.time() - self.last_query_time
        if time_delta > QUERY_INTERVAL:
            return 0
        else:
            return QUERY_INTERVAL - time_delta
    
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

    def build_serverlist_embed( self, list ):
        # Sort according to player count
        serverlist = sorted( list, key=lambda item: item['player_count'], reverse = True )
        # I just had a deja vu... ABOUT THIS EXACT CODE AND ME EXPLAINING IT IN THIS COMMENT
        # FREE WILL IS A LIE
        # WE LIVE IN A SIMULATION
        em = discord.Embed(
            title = LIST_EMBED_TITLE,
            description = "%i server(s) online" % len( list ),
            colour = LIST_EMBED_COLOR )
        counter = 0
        for info in serverlist:
            em.add_field(
                name = "{player_count}/{max_players} | {server_name}".format( **info ),
                value = "Map: {map} | Connect: steam://connect/{address}".format( **info ),
                inline = False )
            counter += 1
            if counter >= LIST_EMBED_MAX:
                break
        return em
    
    async def send_newlist( self, channel, list ):
        self.num_other_msgs = 0
        self.last_print_time = self.last_action_time = time.time()
        self.cur_msg = await BOT.client.send_message( channel, embed = self.build_serverlist_embed( list ) )
        print( "Printed new list." )
        
    async def send_editlist( self, list ):
        self.last_action_time = time.time()
        await BOT.client.edit_message( self.cur_msg, embed = self.build_serverlist_embed( list ) )
        print( "Edited existing list." )


BOT.run( open( "token.txt" ).read(),
    [
    ZMServerList( open( "channel.txt" ).read() )
    # Add other tasks here
    ] )
