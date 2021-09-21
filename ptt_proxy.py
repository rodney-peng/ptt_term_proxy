import sys
import os
import signal
import asyncio
import socket
import traceback
import copy
import time
from dataclasses import dataclass

from mitmproxy import http, ctx
from mitmproxy.proxy import layer, layers

from ptt_event import ProxyEvent
from ptt_terminal import PttTerminal, NotificationRendition
from ptt_macro import MacroContext
import ptt_macro

class PttFlow:

    MAX_CUT_TIME = 2    # in seconds

    def __init__(self, flow):
        self.flow = flow
        self.terminal = PttTerminal(128, 32)

        self.msg_to_terminal = b''
        self.terminal_event = asyncio.Event()
        self.terminal_task = asyncio.create_task(self.terminal_msg_timeout(flow, self.terminal_event))

        self.msg_to_server = []
        self.server_event = asyncio.Event()
        self.server_task = asyncio.create_task(self.server_msg_sender(flow, self.server_event))

        self.clientToServer = True
        self.serverToClient = True
        self.stream_cut_time = 0

        self.macro = None
        self.macro_event = asyncio.Event()
        self.macro_task = None

    def done(self):
        self.terminal_event.clear()
        if not self.terminal_task.done():
            self.terminal_task.cancel()

        self.server_event.clear()
        if not self.server_task.done():
            self.server_task.cancel()

        self.macro_event.clear()
        if self.macro_task and not self.macro_task.done():
            self.macro_task.cancel()

    @dataclass
    class EventContext:
        dropContent: bool = False
        replaceContent: bytes = None
        insertToClient: bytes = b''
        sendToClient:   bytes = b''
        insertToServer: bytes = b''
        sendToServer:   bytes = b''

    def terminal_events(self, lets_do_it, evctx: EventContext):
        for event in lets_do_it:
#            print("proxy.terminal:", event)
            if event._type == ProxyEvent.CUT_STREAM:
                self.clientToServer = False
                self.serverToClient = False
                self.stream_cut_time = time.time()
            elif event._type == ProxyEvent.RESUME_STREAM:
                if not self.stream_cut_time:
                    print("resume stream without cut!", file=sys.stderr)
                self.clientToServer = True
                self.serverToClient = True
                self.stream_cut_time = 0
            elif event._type == ProxyEvent.RUN_MACRO:
                self.macro = getattr(ptt_macro, event.content)
                print("Run macro:", event.content)
            elif event._type == ProxyEvent.DROP_CONTENT:
                evctx.dropContent = True
            elif event._type == ProxyEvent.REPLACE_CONTENT:
                evctx.replaceContent = event.content
            elif event._type == ProxyEvent.INSERT_TO_CLIENT:
                evctx.insertToClient += event.content
            elif event._type == ProxyEvent.SEND_TO_CLIENT:
                evctx.sendToClient += event.content
            elif event._type == ProxyEvent.INSERT_TO_SERVER:
                evctx.insertToServer += event.content
            elif event._type == ProxyEvent.SEND_TO_SERVER:
                evctx.sendToServer += event.content
            elif event._type == ProxyEvent.WARNING:
                print("\n!!!", event, file=sys.stderr)
            else:
                yield event

    def client_message(self, flow_msg):
        lets_do_it = self.terminal.client_message(flow_msg.content)
        evctx = self.EventContext()
        for event in self.terminal_events(lets_do_it, evctx):
            print("proxy.terminal.client:", event)

        if evctx.insertToClient or evctx.sendToClient:
            self.sendToClient(evctx.insertToClient + evctx.sendToClient)

        if evctx.replaceContent:
            flow_msg.content = evctx.replaceContent
            print("Replace client message!", len(flow_msg.content))

        if evctx.insertToServer or evctx.sendToServer:
            flow_msg.content = evctx.insertToServer + (flow_msg.content if not evctx.dropContent else b'') + evctx.sendToServer
            print("Alter client message!", len(flow_msg.content))
        elif evctx.dropContent:
            print("Drop client message!")
            flow_msg.drop()

    def purge_terminal_message(self, event: asyncio.Event, evctx: EventContext):
        event.clear()

        lets_do_it = self.terminal.server_message(self.msg_to_terminal)
        for event in self.terminal_events(lets_do_it, evctx):
            print("proxy.terminal.server:", event)

        self.msg_to_terminal = b''

        if self.macro_task and not self.macro_task.done():
            lets_do_it = self.terminal.lets_do_notifyClient("macro running!")
            for event in self.terminal_events(lets_do_it, evctx):
                print("proxy.macro_running:", event)
            self.macro_event.set()

        if self.macro:
            if self.macro_task and not self.macro_task.done():
                self.macro_task.cancel()
            self.macro_event.clear()
            self.macro_task = asyncio.create_task(self.run_macro(self.flow, self.macro, self.macro_event, self.macro_done))
            self.macro = None

    def server_message(self, flow_msg):
        evctx = self.EventContext()

        firstSegment = not self.msg_to_terminal
        lastSegment  = len(flow_msg.content) < 1021

        if firstSegment:
            lets_do_it = self.terminal.pre_server_message()
            for event in self.terminal_events(lets_do_it, evctx):
                print("proxy.terminal.pre_server:", event)

        self.msg_to_terminal += flow_msg.content

        if not self.serverToClient:
#            print("proxy.server_message: drop server", len(flow_msg.content))
            flow_msg.content = b''

        if lastSegment:
            self.purge_terminal_message(self.terminal_event, evctx)

            if evctx.sendToClient:
                flow_msg.content += evctx.sendToClient
        else:
            self.terminal_event.set()

        if evctx.insertToServer or evctx.sendToServer:
            self.sendToServer(evctx.insertToServer + evctx.sendToServer)

        if firstSegment and evctx.insertToClient:
            flow_msg.content = evctx.insertToClient + flow_msg.content

    async def terminal_msg_timeout(self, flow: http.HTTPFlow, event: asyncio.Event):
        print("terminal_msg_timeout() started, socket opened:", (flow.websocket.timestamp_end is None))

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
                    await asyncio.sleep(0.25)    # tried 0.1 but pending occasionally
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
                evctx = self.EventContext()
                self.purge_terminal_message(event, evctx)

                self.terminal.showScreen()

                assert not evctx.dropContent
                assert evctx.replaceContent is None
                if evctx.insertToClient or evctx.sendToClient:
                    self.sendToClient(evctx.insertToClient + evctx.sendToClient)
                if evctx.insertToServer or evctx.sendToServer:
                    self.sendToServer(evctx.insertToServer + evctx.sendToServer)
            else:
                event.clear()

        print("terminal_msg_timeout() finished")

    def sendToClient(self, data: bytes):
        ctx.master.sendToClient(self.flow, data)

    def sendToServer(self, data: bytes):
        self.msg_to_server.append(data)
        self.server_event.set()

    server_msg_interval = 0.25   # seconds

    # rate-control for message to server
    async def server_msg_sender(self, flow: http.HTTPFlow, event: asyncio.Event):
        print("server_msg_sender() started, socket opened:", (flow.websocket.timestamp_end is None))

        cancelled = False
        while (flow.websocket.timestamp_end is None) and not cancelled:
            try:
                await event.wait()
            except asyncio.CancelledError:
                cancalled = True
                break
            except Exception:
                traceback.print_exc()

            while not cancelled and len(self.msg_to_server):
                try:
                    await asyncio.sleep(self.server_msg_interval)
                    ctx.master.sendToServer(flow, self.msg_to_server.pop(0))
                except asyncio.CancelledError:
                    cancalled = True
                    break
                except Exception:
                    traceback.print_exc()

            event.clear()

        print("server_msg_sender() finished")

    # all messages, self-injected message still has mark
    def preview_message(self, flow_msg):
        if (not flow_msg.from_client): print("wsmsg to client:", len(flow_msg.content))

        if 0 < self.stream_cut_time < time.time() - self.MAX_CUT_TIME:
            print("Maximum cut time exceeded, resume stream!!!", file=sys.stderr)
            self.clientToServer = True
            self.serverToClient = True
            self.stream_cut_time = 0

    # no self-injected to-client message
    def handle_message(self, flow_msg):
        if flow_msg.from_client:
            # injected to server will always pass
            if ctx.master.is_self_injected(flow_msg) or self.clientToServer:
                self.client_message(flow_msg)
            else:
                print("\nclient dropped:", flow_msg.content)
                flow_msg.drop()
        else:
            self.server_message(flow_msg)
        if not flow_msg.content:
#            print("proxy.handle_message: empty content!", "client" if flow_msg.from_client else "server")
            flow_msg.drop()

    def macro_done(self, macros, error = None):
        self.clientToServer = True

        evctx = self.EventContext()
        if error is None:
            lets_do_it = self.terminal.lets_do_notifyClient("macro done!", NotificationRendition(width=20, blink=True))
        else:
            print(error, file=sys.stderr)
            lets_do_it = self.terminal.lets_do_notifyClient(error, NotificationRendition(fg='red', center=True))
        for event in self.terminal_events(lets_do_it, evctx):
            print("proxy.macro_done:", event)
        if evctx.insertToClient or evctx.sendToClient:
            self.sendToClient(evctx.insertToClient + evctx.sendToClient)

    def show_task_exception(self, task):
        if task:
            exc = task.exception()
            if exc:
                print(exc)
                traceback.print_tb(getattr(exc, "__traceback__", None))

    async def run_macro(self, flow: http.HTTPFlow, macros, event: asyncio.Event, doneHook=None):
        print("run_macro() started, socket opened:", (flow.websocket.timestamp_end is None))

        ctx = MacroContext(event, self.server_msg_interval + 1.0)
        error = None
        i = 0
        while (flow.websocket.timestamp_end is None) and i < len(macros) and error is None:
            status = await macros[i].run(self.sendToServer, self.terminal, ctx)
            if isinstance(status, str):
                error = status
            elif status is False:
                await event.wait()
            else:
                i += 1

        if doneHook: doneHook(macros, error)
        print("run_macro() finished")


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

    cmd_formats = {'.':  "self.ptt_flow.terminal.{data}",
                   '?':  "print(self.ptt_flow.terminal.{data})",
                   '#':  "self.ptt_flow.{data}",
                   '$':  "print(self.ptt_flow.{data})",
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

        self.pttFlows[flow] = self.ptt_flow = PttFlow(flow)

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

        self.pttFlows[flow].preview_message(flow_msg)

        # message from client or injected to server will pass
        if (not flow_msg.from_client) and ctx.master.is_self_injected(flow_msg): return

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

