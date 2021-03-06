import sys
import os
import signal
import asyncio
import socket
import traceback
import copy

from mitmproxy import http, ctx
from mitmproxy.proxy import layer, layers

from user_event import UserEvent
import ptt_term

pttTerm = ptt_term.PttTerm(128, 32)


class PttProxy:

    def __init__(self):
        self.reset()
        self.wslayer = None
        self.is_running = False
        self.is_done = False

        # only immutable attribute refers to new object by assignment but PttProxy.last_cmds is not
        self.last_cmds = copy.copy(self.last_cmds)

    def reset(self):
        self.firstSegment = False
        self.lastSegment  = False
        self.server_msgs = bytes()      # to be feed to the screen
        self.standby_msgs = bytes()     # to be sent to the client

        # server task depends on flow
        if hasattr(self, "server_task") and not self.server_task.done():
            self.server_task.cancel()
        if hasattr(self, "server_event") and self.server_event.is_set():
            self.server_event.clear()

    # feed to the screen
    # event must be checked if not None
    def purge_server_message(self, event: asyncio.Event):
        event.clear()

        if len(self.server_msgs):
            pttTerm.pre_refresh()
            pttTerm.feed(self.server_msgs)
            pttTerm.post_refresh()
            self.server_msgs = bytes()

        if len(self.standby_msgs): event.set()

    # send to the client
    def purge_standby_message(self, flow: http.HTTPFlow):
        if len(self.standby_msgs):
            ctx.master.sendToClient(flow, self.standby_msgs)
            self.standby_msgs = bytes()

    def server_message(self, content):
        self.server_msgs += content

        if self.firstSegment: pttTerm.pre_update()

        n = len(content)
#        print("\nserver: (%d)" % n)

        # dirty trick to identify the last segment with size
        # (FIXME) Done: handled in server_msg_timeout()
        # but sometimes a segment with size 1021 is not the last or the last segment is larger than 1021
        # (FIXME) Done: queue message segments in server_msgs
        # a double-byte character could be split into two segments
        if self.lastSegment:
            self.purge_server_message(self.server_event)
        else:
            self.server_event.set()

    vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
    xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
    def client_message(self, content):
        print("\nclient:", content)

        if len(content) > 1 or not UserEvent.isViewable(content[0]):
            # need to reset userEvent for unknown keys otherwise PttTerm.pre_refresh() would go wrong
            pttTerm.userEvent(UserEvent.Unknown)

        uncommitted = (len(content) > 1 and content[-1] == ord('\r'))

        # VT100 escape
        sESC = '\x1b'
        sCSI = '['
        sNUM = '0'

        # Telnet escape
        IAC = 0xff
        SUB = 0xfa
        NOP = 0xf1
        SUBEND = 0xf0
        WINSIZE = 0x1f

        state = None
        number = ""
        n = 0
        while n < len(content):
            resp = None
            b = content[n]

            if state != sNUM: number = ""

            c = chr(b)
            if state == None:
                cmdBegin = n
                if c == sESC:
                    state = sESC
                elif c == '\b':
                    resp = pttTerm.userEvent(UserEvent.Key_Backspace)
                elif c == '\r':
                    resp = pttTerm.userEvent(UserEvent.Key_Enter)
                elif b == IAC:
                    state = IAC
                elif UserEvent.isViewable(b):
                    resp = pttTerm.userEvent(b)
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    if c == 'A':
                        resp = pttTerm.userEvent(UserEvent.Key_Up, uncommitted)
                    elif c == 'B':
                        resp = pttTerm.userEvent(UserEvent.Key_Down, uncommitted)
                    elif c == 'C':
                        resp = pttTerm.userEvent(UserEvent.Key_Right)
                    elif c == 'D':
                        resp = pttTerm.userEvent(UserEvent.Key_Left)
                    elif c == 'F':
                        resp = pttTerm.userEvent(UserEvent.Key_End)
                    elif c == 'H':
                        resp = pttTerm.userEvent(UserEvent.Key_Home)
                    else:
                        print("xterm key:", self.xterm_keys[b - ord('A')])
                elif '0' <= c <= '9':
                    state = sNUM
                    number += c
                    n += 1
                    continue
                state = None
            elif state == sNUM:
                if '0' <= c <= '9':
                    number += c
                    n += 1
                    continue
                elif c == '~':
                    number = int(number)
                    if number == 5:
                        resp = pttTerm.userEvent(UserEvent.Key_PgUp)
                    elif number == 6:
                        resp = pttTerm.userEvent(UserEvent.Key_PgDn)
                    elif number in [1, 7]:
                        resp = pttTerm.userEvent(UserEvent.Key_Home)
                    elif number in [4, 8]:
                        resp = pttTerm.userEvent(UserEvent.Key_End)
                    elif 1 <= number <= len(self.vt_keys):
                        print("vt key:", self.vt_keys[number-1])
                state = None
            elif state == IAC:
                if SUB <= b < IAC:
                    state = SUB
                elif b == SUBEND or b == NOP:
                    state = None
                else:
                    break
            elif state == SUB:
                if b == WINSIZE:
                    if n + 4 < len(content):
                        width  = (content[n+1] << 8) | content[n+2]
                        height = (content[n+3] << 8) | content[n+4]
                        print("Window size", width, height)
                        pttTerm.resize(width, height)
                        n += 4
                        state = None
                    else:
                        break
                elif 0 <= b <= 3:
                    state = None
                else:
                    break

            if isinstance(resp, bytes):
                # replace the current input with resp
                content = content[:cmdBegin] + resp + content[n+1:]
            elif resp is False:
                return False

            n += 1

        return content

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

            self.purge_standby_message(flow)

            rcv_len = len(self.server_msgs)
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
                if len(self.server_msgs) <= rcv_len:
                    break

            if cancelled: break

            if len(self.server_msgs):
                print("Server event timeout! Pending:", len(self.server_msgs))

            self.purge_server_message(event)

        print("server_msg_timeout() finished")

    # self-defined hooks

    def on_signal(self, signum: int):
        print("Addon got", signum, "(%d)" % int(signum))
        print("server_msgs:", len(self.server_msgs))
        if hasattr(self, "server_event"):
            print("server_event:", self.server_event)
        if hasattr(self, "server_task"):
            print("server_task:", self.server_task)
        pttTerm.showState()

    cmd_formats = {'.':  "pttTerm.{data}",
                   '?':  "print(pttTerm.{data})",
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
        class ProxyFlow:

            @staticmethod
            def sendToServer(data):
                ctx.master.sendToServer(flow, data)

            @staticmethod
            def insertToClient(data):
                if self.firstSegment:
                    # insert ahead of the first segment
                    print("Insert to client: ", len(data))
                    self.current_message.content = data + self.current_message.content
                else:
                    self.standby_msgs += data
                    print("Queued to insert: ", len(data))

            @staticmethod
            def sendToClient(data):
                if self.lastSegment:
                    # piggyback to the last segment
                    print("Piggyback to client: ", len(data))
                    self.current_message.content += data
                else:
                    self.standby_msgs += data
                    print("Queued to send: ", len(data))

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

        if not hasattr(self, "server_event"):
            self.server_event = asyncio.Event()
            self.server_task = asyncio.create_task(self.server_msg_timeout(flow, self.server_event))
        pttTerm.flowStarted(ProxyFlow, self.read_flow)

    def websocket_end(self, flow: http.HTTPFlow):
        print("websocket_end")
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

        if flow_msg.from_client:
            self.firstSegment = False
            self.lastSegment  = False

            self.purge_standby_message(flow)
            self.purge_server_message(self.server_event)

            resp = self.client_message(flow_msg.content)
            if isinstance(resp, bytes):
                if resp != flow_msg.content: print("replace client message:", resp)
                flow_msg.content = resp
            else:
                print("Drop client message!")
                flow_msg.drop()
        else:
            self.firstSegment = not self.server_event.is_set() # (len(flow.websocket.messages) == 1 or flow.websocket.messages[-2].from_client)
            self.lastSegment  = (len(flow_msg.content) < 1021) # see the comment in server_message() for why it's 1021
            self.current_message = flow_msg

            original_content = flow_msg.content

            self.server_message(flow_msg.content)

            if self.current_message.content is not original_content:
                print("server -> client, changed:", len(self.current_message.content))

            del self.current_message
            self.firstSegment = False
            self.lastSegment  = False

    def websocket_handshake(self, flow: http.HTTPFlow):
        """
            Called when a client wants to establish a WebSocket connection. The
            WebSocket-specific headers can be manipulated to alter the
            handshake. The flow object is guaranteed to have a non-None request
            attribute.
        """
        print("websocket_handshake")

    def websocket_error(self, flow: http.HTTPFlow):
        """
            A websocket connection has had an error.
        """
        print("websocket_error", flow)

def reload(oldproxy, oldterm):
    from mitmproxy.addonmanager import Loader
    pttTerm.reload(oldterm)

    addons[0].load(Loader(ctx.master))
    addons[0].configure({'termlog_verbosity', 'flow_detail'})

    addons[0].httplayer = getattr(oldproxy, "httplayer", None)
    addons[0].wslayer   = getattr(oldproxy, "wslayer", None)

    print("self.wslayer: ", addons[0].wslayer)

    if addons[0].wslayer:
        addons[0].websocket_start(addons[0].wslayer.flow)
        # only valid in inPanel state
        pttTerm.post_refresh()

    addons[0].running()

addons = [
    PttProxy()
]

