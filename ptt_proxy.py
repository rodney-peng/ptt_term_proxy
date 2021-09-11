import sys
import os
import signal
import asyncio
import socket
import traceback

from mitmproxy import http, ctx
from mitmproxy.proxy import layer, layers

from user_event import UserEvent
import ptt_term

pttTerm = ptt_term.PttTerm(128, 32)


class PttProxy:

    sock_filename = os.path.join(os.path.normpath("/"), "tmp", ".ptt_proxy")

    def __init__(self):
        self.reset()
        self.is_done = False

    def reset(self):
        self.server_msgs = bytes()      # to be feed to the screen
        self.standby_msgs = bytes()     # to be sent to the client
        if hasattr(self, "server_task") and not self.server_task.done():
            self.server_task.cancel()
        if hasattr(self, "server_event") and self.server_event.is_set():
            self.server_event.clear()
        if hasattr(self, "sock_server"):
            self.sock_server.close()
        if hasattr(self, "sock_task") and not self.sock_task.done():
            self.sock_task.cancel()

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
        print("\nserver: (%d)" % n)

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

        if len(content) == 1 and UserEvent.isViewable(content[0]):
            return pttTerm.userEvent(content[0])
        else:
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
            b = content[n]

            if state != sNUM:
                number = ""

            c = chr(b)
            if state == None:
                if c == sESC:
                    state = sESC
                elif c == '\b':
                    if not pttTerm.userEvent(UserEvent.Key_Backspace): return False
                elif c == '\r':
                    if not pttTerm.userEvent(UserEvent.Key_Enter): return False
                elif b == IAC:
                    state = IAC
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    if c == 'A':
                        if not pttTerm.userEvent(UserEvent.Key_Up): return False
                        if uncommitted: pttTerm.cursor_up()
                    elif c == 'B':
                        if not pttTerm.userEvent(UserEvent.Key_Down): return False
                        if uncommitted: pttTerm.cursor_down()
                    elif c == 'C':
                        if not pttTerm.userEvent(UserEvent.Key_Right): return False
                    elif c == 'D':
                        if not pttTerm.userEvent(UserEvent.Key_Left): return False
                    elif c == 'F':
                        if not pttTerm.userEvent(UserEvent.Key_End): return False
                    elif c == 'H':
                        if not pttTerm.userEvent(UserEvent.Key_Home): return False
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
                        if not pttTerm.userEvent(UserEvent.Key_PgUp): return False
                    elif number == 6:
                        if not pttTerm.userEvent(UserEvent.Key_PgDn): return False
                    elif number in [1, 7]:
                        if not pttTerm.userEvent(UserEvent.Key_Home): return False
                    elif number in [4, 8]:
                        if not pttTerm.userEvent(UserEvent.Key_End): return False
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
            n += 1
        return True

    cmd_formats = {'.':  "pttTerm.{data}",
                   '?':  "print(pttTerm.{data})",
                   '!':  "{data}",
                   '\\': "print({data})" }

    '''
        Tips for debugging:
        1. first run "dir()" or "vars()" to see what is available, either "self" or "cls" is available most likely
        2. then run "vars(self)" or "vars(cls)" to see what attributes are available
        3. enter the leading character to repeat the last command: '.', '?', '!', backslash
        4. runtime binding, e.g. to debug a class method "a_method" in "module.py":

           a_func(...):                 # ordinary function
           a_bound_func(bound, ...):    # ordinary function takes at least one argument
           class C:
                a_method(self):
                @classmethod
                a_class_method(cls):
                @staticmethod
                a_static_method():

           1. modify the code and bind self.a_method:

            "!import types, module"
            "!self.a_method = types.MethodType(module.a_bound_func, self)     # bound will be an instance
            "!self.__class__.a_method = module.a_bound_func                   # bound will be an instance
            "!self.__class__.a_method = module.C.a_method
            "!self.__class__.a_class_method = classmethod(module.a_bound_func)     # bound will be an class
            "!self.__class__.a_static_method = staticmethod(module.a_func)

            !!! Don't run "!self.a_method = module.C.a_method" !!!

           Please note that the visibility of self.a_method is now in the modified "module.py".

           2. continue to modify, reload and rebinds:

            delete the module file from __pycache__
            "!from importlib import reload"
            "!reload(module)"

           3. rebind a global function:

            "!global a_func; a_func = module.a_func"
    '''
    async def sock_client_task(self, reader, writer):
        from contextlib import redirect_stdout, redirect_stderr

        class _file():
            @staticmethod
            def write(data: str):
                writer.write(data.encode())

            @staticmethod
            def flush():
                pass

        _out = sys.stdout
        _err = sys.stderr

        last_cmds = {'.': None, '?': None, '!': None, '\\': None}
        while True:
            if self.is_done or self.sock_task.done(): break
            writer.write("> ".encode())
            await writer.drain()

            data = await reader.readline()
            if not data: break

            data = data.decode().rstrip('\n').strip()
            if not data: continue
            print("\ncommand:", data)

            if data[0] not in self.cmd_formats:
                data = '\\' + data
            if len(data) > 1:
                cmd = self.cmd_formats[data[0]].format(data=data[1:])
                last_cmds[data[0]] = cmd
            else:
                cmd = last_cmds[data[0]]

            if cmd:
                print("exec:", cmd)
                with redirect_stdout(_file), redirect_stderr(_file):
                    try:
                        exec(cmd)
                    except Exception:
                        traceback.print_exc()
                '''
                try:
                    sys.stdout = _file
                    sys.stderr = _file
                    exec(cmd)
                except Exception:
                    traceback.print_exc()
                finally:
                    sys.stdout = _out
                    sys.stderr = _err
                '''
                await writer.drain()
        writer.close()
        print("sock_client_task finished")

    async def sock_server_task(self):
        print("sock_server_task started,", self.sock_task)

        try:
            self.sock_server = await asyncio.start_unix_server(self.sock_client_task, self.sock_filename)
            await self.sock_server.serve_forever()
        except asyncio.CancelledError:
            print("sock_server_task cancelled!")
        except Exception:
            traceback.print_exc()

        print("sock_server_task finished,", self.sock_task)

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

    # Addon management

    def load(self, loader):
        print(type(self).__qualname__, "loading!")
        self.log_verbosity = "info"
        self.flow_detail = 1
        self.read_flow = False
#        if not hasattr(ctx.master, "conn_watcher"):

    def configure(self, updated):
        if 'termlog_verbosity' in updated: self.log_verbosity = ctx.options.termlog_verbosity
        if 'flow_detail' in updated: self.flow_detail = ctx.options.flow_detail
        if 'rfile' in updated:
            print("rfile:", ctx.options.rfile)
            self.read_flow = bool(ctx.options.rfile)

    def running(self):
        if hasattr(self, "is_running"): return
        print(self, "running!")
        self.is_running = True
        print("log_verbosity:", self.log_verbosity)
        print("flow_detail:", self.flow_detail)
        self.sock_task = asyncio.create_task(self.sock_server_task())
        ptt_term.pop(pttTerm)

    def done(self):
        print(self, "done!")
        self.reset()
        ptt_term.push(pttTerm)
        self.is_done = True

    def on_signal(self, signum):
        print("Addon got", signum, "(%d)" % int(signum))
        print("server_msgs:", len(self.server_msgs))
        if hasattr(self, "server_event"):
            print("server_event:", self.server_event)
        if hasattr(self, "server_task"):
            print("server_task:", self.server_task)
        pttTerm.showState()

    # next_layer() is called to determine the next layer and return in nextlayer.layer
    def next_layer(self, nextlayer: layer.NextLayer):
        _layers = nextlayer.context.layers
        if len(_layers) and isinstance(_layers[-1], layers.HttpLayer):
            self.httplayer = _layers[-1]
            print("HttpLayer.streams:", _layers[-1].streams)
            '''
            # probably don't have stream yet
            if len(_layers[-1].streams) == 1:
                s = list(_layers[-1].streams.values())[0]
                _layers = s.context.layers
                if len(_layers) and isinstance(_layers[-1], layers.WebsocketLayer):
                    self.wslayer = _layers[-1]
            '''

    # Websocket lifecycle

    # reloading the addon script will not run the hook websocket_start()
    # so we cannot initiate self.server_event, self.server_task here
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
            ctx.master.hijackWebsocketLayer(wslayer)

    def websocket_end(self, flow: http.HTTPFlow):
        print("websocket_end")
        pttTerm.reset()
        self.reset()

    def websocket_message(self, flow: http.HTTPFlow):
        """
            Called when a WebSocket message is received from the client or
            server. The most recent message will be flow.messages[-1]. The
            message is user-modifiable. Currently there are two types of
            messages, corresponding to the BINARY and TEXT frame types.
        """
        class ProxyFlow:

            @staticmethod
            def sendToServer(data):
                ctx.master.sendToServer(flow, data)

            @staticmethod
            def insertToClient(data):
                if self.firstSegment:
                    # insert ahead of the first segment
                    self.current_message.content = data + self.current_message.content
                else:
                    self.standby_msgs += data

            @staticmethod
            def sendToClient(data):
                if self.lastSegment:
                    # piggyback to the last segment
                    self.current_message.content += data
                else:
                    self.standby_msgs += data

        if self.is_done: return

        if not hasattr(self, "server_event"):
            self.server_event = asyncio.Event()
            self.server_task = asyncio.create_task(self.server_msg_timeout(flow, self.server_event))
            pttTerm.flowStarted(ProxyFlow, self.read_flow)

        assert flow.websocket is not None

        flow_msg = flow.websocket.messages[-1]
        if ctx.master.is_self_injected(flow_msg): return

        if flow_msg.from_client:
            self.firstSegment = False
            self.lastSegment  = False

            self.purge_standby_message(flow)
            self.purge_server_message(self.server_event)

            if not self.client_message(flow_msg.content):
                print("Drop client message!")
                flow_msg.drop()
        else:
            self.firstSegment = not self.server_event.is_set() # (len(flow.websocket.messages) == 1 or flow.websocket.messages[-2].from_client)
            self.lastSegment  = (len(flow_msg.content) < 1021) # see the comment in server_message() for why it's 1021
            self.current_message = flow_msg

            self.server_message(flow_msg.content)

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


addons = [
    PttProxy()
]

