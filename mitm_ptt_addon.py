import sys
import os
import re
import pyte
import asyncio
import time

from mitmproxy import http, ctx

from uao import register_uao
register_uao()

from user_event import UserEvent
from ptt_thread import PttThread
from ptt_persist import PttPersist

# fix for double-byte character positioning and drawing
class MyScreen(pyte.Screen):

    def draw(self, char):
        # the current character won't be null, will it?
        #     assert self.buffer[self.cursor.y][self.cursor.x].data != ''
        super(MyScreen, self).draw(char)

        # the cursor will not be at the last column, won't it?
        #     assert self.cursor.x < self.columns
        if ord(char) > 0xff:
            super(MyScreen, self).draw('')


# for event debugging
class MyDebugStream(pyte.DebugStream):

    def feed(self, chars):
        # DebugStream inherits ByteStream and feed() takes bytes but not string
        # re-route to Stream.feed()
        super(pyte.ByteStream, self).feed(chars)


def showScreen(screen):
    lines = screen.display
    print("Cursor:", screen.cursor.y, screen.cursor.x, "Lines:", len(lines))
    for n, line in enumerate(lines, 1):
        print("%2d" % n, "'%s'" % line)

def showCursor(screen):
    print("Cursor:", screen.cursor.y, screen.cursor.x, "'%s'" % screen.display[screen.cursor.y])


class ProxyFlow:

    def __init__(self, master, flow):
        self.master = master
        self.flow = flow

    def sendToServer(self, data):
        assert isinstance(data, bytes)
        print("sendToServer:", data)
        to_client = False
        is_text = False
        self.master.commands.call("inject.websocket", self.flow, to_client, data, is_text)


class PttTerm:

    class _State:
        Unknown = 0
        Waiting = 1
        InPanel = 2
        InBoard = 3
        InThread = 4

    persistor = PttPersist()

    def __init__(self, screen):
        self.reset()
        self.screen = screen
        self.thread = PttThread()

        if not self.persistor.is_connected():
            self.persistor.connect()
        print("persistor:", self.persistor.is_connected())

    def reset(self):
        self.flow = None
        self.state = self._State.Unknown
        self.event = UserEvent.Unknown
        if hasattr(self, "thread"):
            self.thread.clear()
        if hasattr(self, "macro_task") and not self.macro_task.done():
            self.macro_task.cancel()
            del self.macro_task

    def flowStarted(self, flow: ProxyFlow, from_file: bool):
        self.flow = flow
        self.read_flow = from_file

    def flowStopped(self):
        self.flow = None

    def showState(self):
        print("state:", self.state)
        if hasattr(self, "macro_event"): print("macro event:", self.macro_event)
        if hasattr(self, "macro_task"): print("macro task:", self.macro_task)
        if self.state == self._State.InThread:
            self.thread.show(False)
        print("persistor:", self.persistor.is_connected())

    def showThread(self):
        if self.state == self._State.InThread:
            self.thread.show()

    macros_pmore_config = [
#        {'data': b' ', 'state': _State.Unknown},
        {'data': b'\x1a', 'state': _State.InPanel}, # Ctrl-Z
        {'data': b'b', 'state': [_State.InPanel, _State.InBoard]},   # will send to the board SYSOP if no board is viewed previously
        {'data': b' ', 'state': _State.InBoard, 'timeout': True},    # skips the onboarding screen or allows timeout
        {'data': b'\r', 'state': [_State.InBoard, _State.InThread], 'timeout': b'\x1b[A', 'retry': 5},  # enters the thread at cursor or retry after cursor Up
        {'data': b'o', 'state': _State.InThread},   # enters thread browser config
        {'data': b'm', 'state': _State.InThread, 'row': -5, 'pattern': '\*顯示', 'retry': 3}, # 斷行符號: 顯示
        {'data': b'l', 'state': _State.InThread, 'row': -4, 'pattern': '\*無', 'retry': 3},   # 文章標頭分隔線: 無
        {'data': b' ', 'state': _State.InThread},   # ends config
        {'data': b'\x1b[D', 'state': _State.InBoard},   # Left and leaves the thread
        {'data': b'\x1a', 'state': _State.InBoard},     # Ctrl-Z
        {'data': b'c', 'state': _State.InPanel},        # goes to 分類看板
        {'data': b'\x1b[D', 'state': _State.InPanel}    # Left and goes to 主功能表
        ]
    def runPmoreConfig(self):
        if hasattr(self, "macro_task") and not self.macro_task.done():
            self.macro_task.cancel()
            while not self.macro_task.done():
                time.sleep(0.1)

        self.thread.enablePersistence(False)
        self.macro_event = asyncio.Event()
        self.macro_task = asyncio.create_task(self.run_macro(self.macros_pmore_config, self.macro_event))

    def persistThread(self, thread):
        if not self.persistor.is_connected():
            self.persistor.connect()
        if self.persistor.is_connected():
            self.persistor.send(thread, PttPersist.TYPE_THREAD)

    def pre_refresh(self):
        if self.state == self._State.InBoard and self.event in [UserEvent.Key_Right, UserEvent.Key_Enter]:
            showCursor(self.screen)     # entering a thread

        if self.state == self._State.InThread and self.thread.isSwitchEvent(self.event):
            self.thread.switch(self.persistThread)

    def post_refresh(self):
        newState = self._refresh()

        if newState in [self._State.Waiting, self._State.Unknown]:
            # TODO: screen already changed but state remains
            if hasattr(self, "macro_task") and not self.macro_task.done():
                self.macro_event.set()
            return

        prevState = self.state
        self.state = newState

        # this is necessary because user can search and jump to board while viewing thread
        if prevState == self._State.InThread and newState != self._State.InThread:
            self.thread.switch(self.persistThread)

        # if flow is read from file, don't run macro
        if not self.read_flow and not hasattr(self, "macro_task"):
            if prevState == self._State.Unknown and newState == self._State.InPanel:
                self.runPmoreConfig()
        elif hasattr(self, "macro_task") and not self.macro_task.done():
            self.macro_event.set()

    def _refresh(self):
        lines = self.screen.display

#        print("top   : '%s'" % lines[0])
#        print("bottom: '%s'" % lines[-1])

        for input_pattern in [".+請?按.+鍵.*繼續", "請選擇", '搜尋.+', '\s*★快速切換']:
            if re.match(input_pattern, lines[-1]):
                print("Waiting input...")
                return self._State.Waiting

        for panel in ['【主功能表】', '【分類看板】', '【看板列表】', '【 選擇看板 】', '【個人設定】']:
            if re.match(panel, lines[0]):
                print("In panel:", panel)
                return self._State.InPanel

        if re.match("\s*文章選讀", lines[-1]):
            try:
                board = re.search("^\s*【板主:.+(看板|系列)《(\w+)》\s*$", lines[0]).group(2)
                print("In board: '%s'" % board)
            except (AttributeError, IndexError):
                print("Board missing: '%s'" % lines[0])

            showCursor(self.screen)
            return self._State.InBoard

        # note the pattern '\ *?\d+' to match variable percentage digits
        browse = re.match("\s*瀏覽.+\(\ *?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", lines[-1])
        if browse:
            percent = int(browse.group(1))
            firstLine = int(browse.group(2))
            lastLine  = int(browse.group(3))
#            print("Browse: %d%%" % percent, "Lines:", firstLine, "~", lastLine)

            if firstLine == 1:
                try:
                    board = re.match("\s+作者\s+.+看板\s+(\w+)\s*$", lines[0]).group(1)
#                    print("Board: '%s'" % board)
                except (AttributeError, IndexError):
                    print("Board missing: '%s'" % lines[0])

                try:
                    title = re.match("\s+標題\s+(\S.+)\s*$", lines[1]).group(1)
#                    print("Title: '%s'" % title)
                except (AttributeError, IndexError):
                    print("Title missing: '%s'" % lines[1])

            self.thread.view(self.screen.display[0:-1], firstLine, lastLine, percent == 100)
            return self._State.InThread

        return self._State.Unknown

    # the client message will be dropped if false is returned
    def userEvent(self, event: UserEvent):
        print("User event:", UserEvent.name(event))

        if event != UserEvent.Unknown and \
           self.state == self._State.InThread and \
           self.thread.is_prohibited(event):
            return False

        self.event = event
        return True

    # return value:
    #   False: to break
    #   True:  to continue
    #   bytes: priority data to send
    #   None:  to loop normally
    def handle_macro_event(self, macro, timeout, priority):
        if isinstance(macro['state'], list):
            if self.state not in macro['state']:
                print("expected state", macro['state'], "but", self.state)
                return False
        elif self.state != macro['state']:
            print("expected state", macro['state'], "but", self.state)
            return False

        if 'timeout' in macro and 'retry' in macro and isinstance(macro['timeout'], bytes):
            if timeout or priority:
                if self.macro_retry < 0: self.macro_retry = macro['retry']
                if self.macro_retry > 0:
                    print("retry", self.macro_retry)
                    if not priority:
                        self.macro_retry -= 1
                        return macro['timeout']
                    else:
                        return True
                else:
                    print("Reach maximum retry!")
                    self.macro_retry = -1
                    return False
            else:
                self.macro_retry = -1

        if 'row' in macro and 'pattern' in macro and 'retry' in macro:
            if re.search(macro['pattern'], self.screen.display[macro['row']]) is None:
                if self.macro_retry < 0: self.macro_retry = macro['retry']
                if self.macro_retry > 0:
                    print("retry", self.macro_retry)
                    self.macro_retry -= 1
                    return True
                else:
                    print("Reach maximum retry!")
                    self.macro_retry = -1
                    return False
            else:
                print("found", macro['pattern'])
                self.macro_retry = -1

        return None

    # macros is list of {data, expected states} pairs, see macros_pmore_config above
    async def run_macro(self, macros, event):
        self.macro_retry = -1
        prio_data = None
        i = 0
        while i < len(macros):
            await asyncio.sleep(0.5)
            macro = macros[i]
            assert ('data' in macro and 'state' in macro)
            if self.flow:
                try:
                    self.flow.sendToServer(prio_data if prio_data else macro['data'])
                except Exception as e:
                    print("sendToServer exception:\n", e)
                    break
            else:
                print("ProxyFlow is unavailable!")
                break

            timeout = False
            try:
                await asyncio.wait_for(event.wait(), 1.0)
            except asyncio.TimeoutError:
                if 'timeout' in macro and macro['timeout']:
                    # ignore timeout and proceed
                    event.set()
                    timeout = True
                else:
                    print("macro event timeout!")
                    break
            except asyncio.CancelledError:
                print("macro event cancelled!")
                break
            except Exception as e:
                print("wait_for exception:\n", e)
                break

            if event.is_set():
                event.clear()
                try:
                    next = self.handle_macro_event(macro, timeout, prio_data is not None)
                except Exception as e:
                    print("handle_macro_event:\n", e)
                    break
                if next is True:
                    prio_data = None
                    continue
                elif next is False:
                    break
                elif isinstance(next, bytes):
                    prio_data = next
                    continue
            else:
                print("macro event is not set")
                break
            prio_data = None
            i += 1

        if macros is self.macros_pmore_config:
            self.thread.enablePersistence(True)

        print("run_macro task finished!")

class SniffWebSocket:

    def __init__(self):
        self.reset()

        self.screen = MyScreen(128, 32)
#        self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)

        self.pttTerm = PttTerm(self.screen)

    def reset(self):
        self.server_msgs = bytes()
        if hasattr(self, "screen"):
            self.screen.reset()
        if hasattr(self, "stream"):
            self.stream.reset()
        if hasattr(self, "server_event") and self.server_event.is_set():
            self.server_event.clear()
        if hasattr(self, "server_task") and not self.server_task.done():
            self.server_task.cancel()
        if hasattr(self, "pttTerm"):
            self.pttTerm.reset()

    def on_SIGUSR1(self):
        print("server_msgs:", len(self.server_msgs))
        if hasattr(self, "server_event"):
            print("server_event:", self.server_event)
        if hasattr(self, "server_task"):
            print("server_task:", self.server_task)
        if hasattr(self, "pttTerm"):
            self.pttTerm.showState()

    def on_SIGUSR2(self):
        showScreen(self.screen)
        if hasattr(self, "pttTerm"):
#            self.pttTerm.runPmoreConfig()
            self.pttTerm.showThread()

    def purge_server_message(self):
        if len(self.server_msgs):
            self.pttTerm.pre_refresh()
            self.stream.feed(self.server_msgs.decode("big5uao", 'replace'))
            self.server_msgs = bytes()
            self.pttTerm.post_refresh()

    def server_message(self, content):
        n = len(content)
        print("\nserver: (%d)" % n)

        self.server_msgs = bytes().join([self.server_msgs, content])

        # dirty trick to identify the last segment with size
        # (FIXME) Done: handled in server_msg_timeout()
        # but sometimes a segment with size 1021 is not the last or the last segment is larger than 1021
        # (FIXME) Done: queue message segments in server_msgs
        # a double-byte character could be split into two segments
        if n < 1021:
            self.server_event.clear()
            self.purge_server_message()
        else:
            self.server_event.set()

    vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
    xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
    def client_message(self, content):
        print("\nclient:", content)

        if self.server_event.is_set():
            self.server_event.clear()
            self.purge_server_message()

        if len(content) == 1 and UserEvent.isViewable(content[0]):
            return self.pttTerm.userEvent(content[0])
        else:
            # need to reset userEvent for unknown keys otherwise PttTerm.pre_refresh() would go wrong
            self.pttTerm.userEvent(UserEvent.Unknown)

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
                elif c == '\r':
                    if not self.pttTerm.userEvent(UserEvent.Key_Enter): return False
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
                        if not self.pttTerm.userEvent(UserEvent.Key_Up): return False
                        if uncommitted: self.screen.cursor_up()
                    elif c == 'B':
                        if not self.pttTerm.userEvent(UserEvent.Key_Down): return False
                        if uncommitted: self.screen.cursor_down()
                    elif c == 'C':
                        if not self.pttTerm.userEvent(UserEvent.Key_Right): return False
                    elif c == 'D':
                        if not self.pttTerm.userEvent(UserEvent.Key_Left): return False
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
                    if 1 <= number <= len(self.vt_keys):
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
                        self.screen.resize(height, width)
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

    async def server_msg_timeout(self, flow, event):
        print("server_msg_timeout() started, socket opened:", (flow.websocket.timestamp_end is None))
        cancelled = False
        while (flow.websocket.timestamp_end is None) and not cancelled:
            try:
                await event.wait()
            except asyncio.CancelledError:
                cancelled = True
            except Exception as e:
                print("server_msg_timeout wait exception\n", e)

            rcv_len = len(self.server_msgs)
            while not cancelled:
                try:
                    await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    cancalled = True
                except Exception as e:
                    print("server_msg_timeout sleep exception\n", e)

                if len(self.server_msgs) <= rcv_len:
                    break

            if len(self.server_msgs):
                print("Server event timeout! Pending:", len(self.server_msgs))

            if event.is_set():
                event.clear()
                self.purge_server_message()

        print("server_msg_timeout() finished")

    # Addon management

    def load(self, loader):
        print("SniffWebSocket loading!")
        self.log_verbosity = "info"
        self.flow_detail = 1
        self.read_flow = False

    def configure(self, updated):
        print("SniffWebSocket configure updated! options:", updated)
        if 'termlog_verbosity' in updated: self.log_verbosity = ctx.options.termlog_verbosity
        if 'flow_detail' in updated: self.flow_detail = ctx.options.flow_detail
        if 'rfile' in updated: self.read_flow = True

    def running(self):
        print("SniffWebSocket running!")
        self.is_running = True
        print("log_verbosity:", self.log_verbosity)
        print("flow_detail:", self.flow_detail)

    def done(self):
        print("SniffWebSocket done!")
        if hasattr(self, "server_task") and not self.server_task.done():
            self.server_task.cancel()
            while not self.server_task.done():
                time.sleep(0.1)

    # Websocket lifecycle

    def websocket_start(self, flow: http.HTTPFlow):
        print("websocket_start")
        self.server_event = asyncio.Event()
        self.server_task = asyncio.create_task(self.server_msg_timeout(flow, self.server_event))
        self.pttTerm.flowStarted(ProxyFlow(ctx.master, flow), self.read_flow)

    def websocket_end(self, flow: http.HTTPFlow):
        print("websocket_end")
        self.reset()

    def websocket_message(self, flow: http.HTTPFlow):
        """
            Called when a WebSocket message is received from the client or
            server. The most recent message will be flow.messages[-1]. The
            message is user-modifiable. Currently there are two types of
            messages, corresponding to the BINARY and TEXT frame types.
        """
        assert flow.websocket

        flow_msg = flow.websocket.messages[-1]
        if flow_msg.from_client:
            if not self.client_message(flow_msg.content):
                print("Drop client message!")
                flow_msg.drop()
        else:
            self.server_message(flow_msg.content)

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
    SniffWebSocket()
]

