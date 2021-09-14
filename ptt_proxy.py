import sys
import os
import signal
import asyncio
import socket
import traceback
import copy
from dataclasses import dataclass

from mitmproxy import http, ctx
from mitmproxy.proxy import layer, layers

from user_event import ProxyEvent
from ptt_terminal import PttTerminal


class PttFlow:

    def __init__(self, flow):
        self.flow = flow
        self.terminal = PttTerminal(128, 32, self)
        self.msg_to_terminal = b''

        self.server_event = asyncio.Event()
        self.server_task = asyncio.create_task(self.server_msg_timeout(flow, self.server_event))

    def done(self):
        self.server_event.clear()
        if not self.server_task.done():
            self.server_task.cancel()

    @dataclass
    class EventContext:
        dropped: bool = False
        replace: bytes = None
        insertToClient: bytes = b''
        sendToClient:   bytes = b''
        insertToServer: bytes = b''
        sendToServer:   bytes = b''

    def terminal_events(self, handler, evctx: EventContext):
        for event in handler:
            if event._type == ProxyEvent.DROP:
                evctx.dropped = True
            elif event._type == ProxyEvent.REPLACE:
                evctx.replace = event.content
            elif event._type == ProxyEvent.INSERT_TO_CLIENT:
                evctx.insertToClient += event.content
            elif event._type == ProxyEvent.SEND_TO_CLIENT:
                evctx.sendToClient += event.content
            elif event._type == ProxyEvent.INSERT_TO_SERVER:
                evctx.insertToServer += event.content
            elif event._type == ProxyEvent.SEND_TO_SERVER:
                evctx.sendToServer += event.content
            else:
                yield event

    def client_message(self, flow_msg):
        handler = self.terminal.client_message(flow_msg.content)
        evctx = self.EventContext()
        for event in self.terminal_events(handler, evctx):
            print("from_client:", event)

        if evctx.insertToClient or evctx.sendToClient:
            ctx.master.sendToClient(self.flow, evctx.insertToClient + evctx.sendToClient)

        if evctx.replace: flow_msg.content = replace

        if evctx.insertToServer or evctx.sendToServer:
            flow_msg.content = evctx.insertToServer + (flow_msg.content if not evctx.dropped else b'') + evctx.sendToServer
            print("Replace client message!", len(flow_msg.content))
        elif evctx.dropped:
            print("Drop client message!")
            flow_msg.drop()

    def purge_terminal_message(self, event: asyncio.Event, evctx: EventContext):
        event.clear()

        handler = self.terminal.server_message(self.msg_to_terminal)
        for event in self.terminal_events(handler, evctx):
            print("from_server:", event)

        self.msg_to_terminal = b''

    def server_message(self, flow_msg):
        evctx = self.EventContext()

        firstSegment = not self.msg_to_terminal
        lastSegment  = len(flow_msg.content) < 1021

        if firstSegment:
            handler = self.terminal.pre_server_message()
            for event in self.terminal_events(handler, evctx):
                print("pre_server:", event)

        self.msg_to_terminal += flow_msg.content

        if lastSegment:
            self.purge_terminal_message(self.server_event, evctx)

            if evctx.sendToClient:
                flow_msg.content += evctx.sendToClient
        else:
            self.server_event.set()

        if evctx.insertToServer or evctx.sendToServer:
            ctx.master.sendToServer(self.flow, evctx.insertToServer + evctx.sendToServer)

        if firstSegment and evctx.insertToClient:
            flow_msg.content = evctx.insertToClient + flow_msg.content

    async def server_msg_timeout(self, flow: http.HTTPFlow, event: asyncio.Event):
        print("server_msg_timeout() started, socket opened:", (flow.websocket.timestamp_end is None))

        cancelled = False
        while (flow.websocket.timestamp_end is None) and not cancelled:
            try:
                await event.wait()
            except asyncio.CancelledError:
                cancalled = True
                break
            except Exception:
                traceback.print_exc()

            rcv_len = len(self.msg_to_terminal)
            if rcv_len == 0:
                event.clear()
                continue

            while not cancelled:
                try:
                    # message could be purged during sleeping
                    await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    cancalled = True
                    break
                except Exception:
                    traceback.print_exc()

                # no more coming data
                if len(self.msg_to_terminal) <= rcv_len:
                    break

            if cancelled: break

            if len(self.msg_to_terminal):
                print("Server event timeout! Pending:", len(self.msg_to_terminal))
                self.purge_terminal_message(event, self.EventContext())
            else:
                event.clear()

        print("server_msg_timeout() finished")

    def handle_message(self, flow_msg):
        if flow_msg.from_client:
            self.client_message(flow_msg)
        else:
            self.server_message(flow_msg)


class PttProxy:

    def __init__(self):
        self.reset()
        self.wslayer = None
        self.is_running = False
        self.is_done = False

        # only immutable attribute refers to new object by assignment but PttProxy.last_cmds is not
        self.last_cmds = copy.copy(self.last_cmds)

    def reset(self):
        for flow in getattr(self, "pttFlows", {}).values():
            flow.done()

        self.pttFlows = {}

    # self-defined hooks

    def on_signal(self, signum: int):
        print("Addon got", signum, "(%d)" % int(signum))

    cmd_formats = {'.':  "list(self.pttFlows.values())[0].terminal.{data}",
                   '?':  "print(list(self.pttFlows.values())[0].terminal.{data})",
                   '!':  "{data}",
                   '\\': "print({data})" }

    last_cmds = {'.': None, '?': None, '!': None, '\\': None}

    # exception is handled on caller
    def on_debug_command(self, data: str, lookup: callable):
        command = lookup(self.cmd_formats, self.last_cmds, data)
        if command: exec(command)

    # Addon management

    def load(self, loader):
        print(type(self).__qualname__, "loading!")
        self.log_verbosity = "info"
        self.flow_detail = 1
        self.read_flow = False

    def configure(self, updated):
        if 'termlog_verbosity' in updated: self.log_verbosity = ctx.options.termlog_verbosity
        if 'flow_detail' in updated: self.flow_detail = ctx.options.flow_detail
        if 'rfile' in updated:
            print("rfile:", ctx.options.rfile)
            self.read_flow = bool(ctx.options.rfile)

    def running(self):
        if self.is_running: return

        print(self, "running!")
        self.is_running = True
        print("log_verbosity:", self.log_verbosity)
        print("flow_detail:", self.flow_detail)

    # the addon is still loaded even after done()
    # it just will not receive event from the addon manager
    def done(self):
        print(self, "done!")
        self.reset()
        self.is_done = True

    # next_layer() is called to determine the next layer and return in nextlayer.layer
    def next_layer(self, nextlayer: layer.NextLayer):
        _layers = nextlayer.context.layers
        if len(_layers) and isinstance(_layers[-1], layers.HttpLayer):
            self.httplayer = _layers[-1]
            print("HttpLayer.streams:", _layers[-1].streams)

    # Websocket lifecycle

    # reloading the addon script will not run the hook websocket_start()
    def websocket_start(self, flow: http.HTTPFlow):
        print("websocket_start")
        wslayer = getattr(self, "wslayer", None)
        httplayer = getattr(self, "httplayer", None)
        if not wslayer and httplayer:
            print("HttpLayer.streams:", httplayer.streams)
            if len(httplayer.streams) == 1:
                s = list(httplayer.streams.values())[0]
                _layers = s.context.layers
                if len(_layers) and isinstance(_layers[-1], layers.WebsocketLayer):
                    self.wslayer = wslayer = _layers[-1]
        if wslayer:
            print(wslayer)
            ctx.master.websocketLayerStarted(wslayer)

        self.pttFlows[flow] = PttFlow(flow)

    def websocket_end(self, flow: http.HTTPFlow):
        print("websocket_end")

        self.pttFlows[flow].done()
        del self.pttFlows[flow]

        if getattr(self, "wslayer", None):
            ctx.master.websocketLayerEnded(self.wslayer)
            self.httplayer = None
            self.wslayer = None

    def websocket_message(self, flow: http.HTTPFlow):
        """
            Called when a WebSocket message is received from the client or
            server. The most recent message will be flow.messages[-1]. The
            message is user-modifiable. Currently there are two types of
            messages, corresponding to the BINARY and TEXT frame types.
        """
        if not self.is_running or self.is_done: return

        flow_msg = flow.websocket.messages[-1]
        if ctx.master.is_self_injected(flow_msg): return

        self.pttFlows[flow].handle_message(flow_msg)

def reload(oldproxy):
    from mitmproxy.addonmanager import Loader

    addons[0].load(Loader(ctx.master))
    addons[0].configure({'termlog_verbosity', 'flow_detail'})

    addons[0].httplayer = getattr(oldproxy, "httplayer", None)
    addons[0].wslayer   = getattr(oldproxy, "wslayer", None)

    print("self.wslayer: ", addons[0].wslayer)

    if addons[0].wslayer:
        addons[0].websocket_start(addons[0].wslayer.flow)

    addons[0].running()

addons = [
    PttProxy()
]

