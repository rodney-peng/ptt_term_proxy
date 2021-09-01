import sys
import os
import re
import pyte
import asyncio
from time import sleep

from mitmproxy import http, ctx

from uao import register_uao
register_uao()

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


class UserEvent:
    Unknown = 0
    Key_Up = 1
    Key_Down = 2
    Key_Right = 3
    Key_Left = 4
    Key_Enter = 5
    # 32 ~ 126 is the same viewable ASCII code

    def isViewable(event: int):
        return ' ' <= chr(event) <= '~'

    def name(event: int):
        assert event >= 0
        return ["-", "Up", "Down", "Right", "Left", "Enter"][event] if event <= UserEvent.Key_Enter else chr(event)

class PttThread:

    def __init__(self):
        self.clear()

    def clear(self):
        self.lines = []
        self.lastViewed = 0
        self.url = None
        self.atBegin = self.atEnd = False

    LINE_HOLDER = chr(0x7f)

    def view(self, lines, first: int, last: int, atEnd: bool):
        self.atBegin = (first == 1)
        self.atEnd = atEnd

        if self.lastViewed < last:
            # which is better?
            #   [self.LINE_HOLDER for _ in range(last - self.lastViewed)]
            #   self.LINE_HOLDER * (last - self.lastViewed)
            self.lines.extend(self.LINE_HOLDER * (last - self.lastViewed))
            self.lastViewed = last

        print("View lines:", first, last, "curr:", len(self.lines), self.lastViewed)

        i = 0
        text = ""
        while i < len(lines) and first <= last:
            line = lines[i].rstrip()
            if len(line.encode("big5uao", "replace")) > 78 and line[-1] == '\\':
                text += line[0:-1]
            else:
                self.lines[first-1] = text + line
#                print("add [%d]" % first, "'%s'" % self.lines[first-1])
                text = ""
                first += 1
            i += 1

        if text and first <= last:
            self.lines[first-1] = text
#            print("add [%d]" % first, "'%s'" % self.lines[first-1])
            first += 1

        if first <= last:
            print("\nCaution: line wrap is probably missing!\n")

    def text(self, first = 1, last = -1):
#        print("text:", first, last, self.lastViewed)
        if first < 0: first = self.lastViewed + 1 + first
        if last < 0: last = self.lastViewed + 1 + last
#        print("text:", first, last, self.lastViewed)

        text = ""
        while 0 < first <= last <= self.lastViewed:
#            print("line [%d]" % first, "'%s'" % self.lines[first-1])
            text += (self.lines[first-1] + '\n')
            first += 1
        return text

    def scanURL(self):
        if self.lastViewed < 3:
            return None
        if self.url:
            return self.url

        i = self.lastViewed - 3
        while i > 0:
            if self.lines[i] == "--" and \
               self.lines[i+1].startswith("※ 發信站: 批踢踢實業坊") and \
               self.lines[i+2].startswith("※ 文章網址:"):
                self.url = (self.lines[i+2])[7:].strip()
                return self.url
            i -= 1

        return None

    def show(self, complete=True):
        url = self.scanURL()
        print("\nThread lines:", self.lastViewed, "url:", url)
        if url:
            board, fn = self.url2fn(url)
            aidc = self.fn2aidc(fn)
            print("board:", board, "fn:", fn, "aidc:", aidc)
        if complete:
            print(self.text())
        else:
            print(self.text(1, 3))
            print(self.text(-3))

        print()

    def saveToFile(self):
        def write(lines, f):
            '''
            merge new and existing content:
            if new line is not empty(has LINE_HOLDER only), always overwrites existing one
            otherwise skip to the next line
            '''
            existing = len(lines)
            for n, text in enumerate(self.lines):
                if text != self.LINE_HOLDER:
                    f.write(text + '\n')
                elif n < existing:
                    f.write(lines[n])
                else:
                    f.write('\n')

            n += 1
            print("Write new lines:", n)
            while n < existing:
                f.write(lines[n])
                n += 1
            print("Write total lines:", n)

        url = self.scanURL()
        if url is None: return

        board, fn = self.url2fn(url)
        aidc = self.fn2aidc(fn)
        pathname = os.path.join("ptt", board)
        filename = f"{aidc}.{fn}"
        fullname = os.path.join(pathname, filename)
        try:
            with open(fullname, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        try:
            if len(lines) == 0: os.makedirs(pathname, mode=0o775, exist_ok=True)
            with open(fullname, "w", encoding="utf-8") as f:
                write(lines, f)
                print("Write", os.path.join(pathname, filename), "bytes", f.tell())
        except Exception as e:
            print(f"Failed to save {fullname}:\n", e)

    def enableSaveOnSwitch(self, enabled=True):
        self.saveOnSwitch = enabled

    def switch(self):
        if not hasattr(self, "saveOnSwitch") or self.saveOnSwitch:
            self.saveToFile()
        self.clear()

    def isSwitchEvent(self, event: UserEvent):
        return (event is not None) and ( \
               (event == UserEvent.Key_Up and self.atBegin) or \
               (event == UserEvent.Key_Down and self.atEnd) or \
               (event == UserEvent.Key_Left) or \
               (chr(event) in "qfb]+[-=tAa") )

    # FN: filename
    # AIDu: uncompressed article number
    # AIDc: compressed article number
    def url2fn(self, url=None):
        if not url:
            url = self.url
        result = re.match("https?://www.ptt.cc/bbs/(.+)/(.+)\.html", url)
        if not result:
            return None
        board = result.group(1)
        fn    = result.group(2)
        return board, fn

    ENCODE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
    def fn2aidc(self, fn):
        result = re.match("(.)\.(\d+)\.A\.([0-9A-F]{3})", fn)
        if not result:
            return None
        m = 0 if result.group(1) == 'M' else 1
        hi = int(result.group(2)) & 0xffffffff
        lo = int(result.group(3), 16) & 0xfff
        aidu = (((m << 32) | hi) << 12) | lo
        aidc = ''
        aidc += self.ENCODE[(m << 2) | (hi >> 30)]
        aidc += self.ENCODE[(hi >> 24) & 0x3f]
        aidc += self.ENCODE[(hi >> 18) & 0x3f]
        aidc += self.ENCODE[(hi >> 12) & 0x3f]
        aidc += self.ENCODE[(hi >>  6) & 0x3f]
        aidc += self.ENCODE[ hi        & 0x3f]
        aidc += self.ENCODE[lo >> 6]
        aidc += self.ENCODE[lo & 0x3f]
        return aidc


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

    def __init__(self, screen):
        self.reset()
        self.screen = screen
        self.thread = PttThread()

    def reset(self):
        self.flow = None
        self.state = self._State.Unknown
        self.event = UserEvent.Unknown
        if hasattr(self, "thread"):
            self.thread.clear()
        if hasattr(self, "macro_task") and not self.macro_task.done():
            self.macro_task.cancel()
            del self.macro_task

    def flowStarted(self, flow: ProxyFlow):
        self.flow = flow

    def flowStopped(self):
        self.flow = None

    def showState(self):
        print("state:", self.state)
        if hasattr(self, "macro_event"): print("macro event:", self.macro_event)
        if hasattr(self, "macro_task"): print("macro task:", self.macro_task)
        if self.state == self._State.InThread:
            self.thread.show(False)

    def showThread(self):
        if self.state == self._State.InThread:
            self.thread.show()

    macros_pmore_config = [
#        {'data': b' ', 'state': _State.Unknown},
        {'data': b'\x1a', 'state': _State.InPanel}, # Ctrl-Z
        {'data': b'b', 'state': [_State.InPanel, _State.InBoard]},   # will send to the board SYSOP for the first time
        {'data': b' ', 'state': _State.InBoard, 'timeout': True},    # skips the onboarding screen otherwise allows timeout
        {'data': b'\r', 'state': _State.InThread},  # enters the thread in focus
        {'data': b'o', 'state': _State.InThread},   # enters thread browser config
        {'data': b'm', 'state': _State.InThread, 'row': -5, 'pattern': '\*顯示', 'retry': 3},
        {'data': b'l', 'state': _State.InThread, 'row': -4, 'pattern': '\*無', 'retry': 3},
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
                sleep(0.1)

        self.thread.enableSaveOnSwitch(False)
        self.macro_event = asyncio.Event()
        self.macro_task = asyncio.create_task(self.run_macro(self.macros_pmore_config, self.macro_event))

    def pre_refresh(self):
        if self.state == self._State.InBoard and self.event in [UserEvent.Key_Right, UserEvent.Key_Enter]:
            showCursor(self.screen)     # entering a thread

        if self.state == self._State.InThread and self.thread.isSwitchEvent(self.event):
            self.thread.switch()

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
            self.thread.switch()

        if not hasattr(self, "macro_task"):
            if prevState == self._State.Unknown and newState == self._State.InPanel:
                self.runPmoreConfig()
        elif not self.macro_task.done():
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

    def userEvent(self, event: UserEvent):
        print("User event:", UserEvent.name(event))
        self.event = event

    # return value:
    #   False: to break
    #   True:  to continue
    #   None:  to loop normally
    def handle_macro_event(self, macro):
        if isinstance(macro['state'], list):
            if self.state not in macro['state']:
                print("expected state", macro['state'], "but", self.state)
                return False
        elif self.state != macro['state']:
            print("expected state", macro['state'], "but", self.state)
            return False
        if 'row' in macro and 'pattern' in macro and 'retry' in macro:
            if re.search(macro['pattern'], self.screen.display[macro['row']]) is None:
                if self.macro_retry < 0: self.macro_retry = macro['retry']
                print("retry", self.macro_retry)
                if self.macro_retry > 0:
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
        i = 0
        while i < len(macros):
            await asyncio.sleep(0.5)
            macro = macros[i]
            assert ('data' in macro and 'state' in macro)
            if self.flow:
                try:
                    self.flow.sendToServer(macro['data'])
                except Exception as e:
                    print("sendToServer exception:\n", e)
                    break
            else:
                print("ProxyFlow is unavailable!")
                break

            try:
                await asyncio.wait_for(event.wait(), 1.0)
            except asyncio.TimeoutError:
                if 'timeout' in macro and macro['timeout']:
                    # pretend event is set and proceed
                    event.set()
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
                    next = self.handle_macro_event(macro)
                except Exception as e:
                    print(e)
                    break
                if next is True:
                    continue
                elif next is False:
                    break
            else:
                print("macro event is not set")
                break
            i += 1

        if macros is self.macros_pmore_config:
            self.thread.enableSaveOnSwitch(True)

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
            self.pttTerm.userEvent(content[0])
            return
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
                    self.pttTerm.userEvent(UserEvent.Key_Enter)
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
                        self.pttTerm.userEvent(UserEvent.Key_Up)
                        if uncommitted: self.screen.cursor_up()
                    elif c == 'B':
                        self.pttTerm.userEvent(UserEvent.Key_Down)
                        if uncommitted: self.screen.cursor_down()
                    elif c == 'C':
                        self.pttTerm.userEvent(UserEvent.Key_Right)
                    elif c == 'D':
                        self.pttTerm.userEvent(UserEvent.Key_Left)
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

    async def server_msg_timeout(self, flow, event):

        print("server_msg_timeout() started, socket opened:", (flow.websocket.timestamp_end is None))
        while flow.websocket.timestamp_end is None:
            try:
                await event.wait()
            except asyncio.CancelledError:
                return
            except Exception as e:
                print("server_msg_timeout wait exception\n", e)

            rcv_len = len(self.server_msgs)
            while True:
                try:
                    await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    return
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

    # Websocket lifecycle

    def websocket_handshake(self, flow: http.HTTPFlow):
        """

            Called when a client wants to establish a WebSocket connection. The

            WebSocket-specific headers can be manipulated to alter the

            handshake. The flow object is guaranteed to have a non-None request

            attribute.

        """
        print("websocket_handshake")

    def websocket_start(self, flow: http.HTTPFlow):
        """

            A websocket connection has commenced.

        """
        print("websocket_start")
        self.server_event = asyncio.Event()
        self.server_task = asyncio.create_task(self.server_msg_timeout(flow, self.server_event))
        self.pttTerm.flowStarted(ProxyFlow(ctx.master, flow))

    def websocket_message(self, flow: http.HTTPFlow):
        """

            Called when a WebSocket message is received from the client or

            server. The most recent message will be flow.messages[-1]. The

            message is user-modifiable. Currently there are two types of

            messages, corresponding to the BINARY and TEXT frame types.

        """
        if flow.websocket:

            flow_msg = flow.websocket.messages[-1]

            if flow_msg.from_client:
                self.client_message(flow_msg.content)
            else:
                self.server_message(flow_msg.content)

    def websocket_error(self, flow: http.HTTPFlow):
        """

            A websocket connection has had an error.

        """
        print("websocket_error", flow)

    def websocket_end(self, flow: http.HTTPFlow):
        """

            A websocket connection has ended.

        """
        print("websocket_end")
        self.reset()

addons = [
    SniffWebSocket()
]

