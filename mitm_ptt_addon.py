import sys
import os
import re
import pyte
import asyncio

from mitmproxy import http

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


def showScreen(screen):
    lines = screen.display
    print("Cursor:", screen.cursor.y, screen.cursor.x, "Lines:", len(lines))
    for n, line in enumerate(lines, 1):
        print("%2d" % n, "'%s'" % line)

def showCursor(screen):
    print("Cursor:", screen.cursor.y, screen.cursor.x, "'%s'" % screen.display[screen.cursor.y])

def locate_from_screen(screen, bBottom = False):
    lines = screen.display

    if bBottom:
#        print("top   : '%s'" % lines[0])
#        print("bottom: '%s'" % lines[-1])

        for input_pattern in [".+請?按.+鍵.*繼續", "請選擇", '搜尋.+', '\s*★快速切換']:
            if re.match(input_pattern, lines[-1]):
                print("Waiting input...")
                return

        for panel in ['【主功能表】', '【分類看板】', '【看板列表】', '【 選擇看板 】', '【個人設定】']:
            if re.match(panel, lines[0]):
                print("In panel")
                return

        if re.match("\s*文章選讀", lines[-1]):
            try:
                board = re.search("^\s*【板主:.+看板《(\w+)》\s*$", lines[0]).group(1)
                print("In board: '%s'" % board)
            except (AttributeError, IndexError):
                print("Board missing: '%s'" % lines[0])

            showCursor(screen)
            return

        # note the pattern '\ *?\d+' to match variable percentage digits
        browse = re.match("\s*瀏覽.+\(\ *?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", lines[-1])
        if browse:
            percent = int(browse.group(1))
            lineStart = int(browse.group(2))
            lineEnd = int(browse.group(3))
            print("Browse: %d%%" % percent, "Lines:", lineStart, "~", lineEnd)

            if (lineStart == 1):
                try:
                    board = re.match("\s+作者\s+.+看板\s+(\w+)\s*$", lines[0]).group(1)
                    print("Board: '%s'" % board)
                except (AttributeError, IndexError):
                    print("Board missing: '%s'" % lines[0])

                try:
                    title = re.match("\s+標題\s+(\S.+)\s*$", lines[1]).group(1)
                    print("Title: '%s'" % title)
                except (AttributeError, IndexError):
                    print("Title missing: '%s'" % lines[1])
            return


class SniffWebSocket:

    def __init__(self):
        print("SniffWebSocket init")
        self.screen = MyScreen(128, 32)
#        self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)
        self.lastServerMsgTime = -1
        self.server_msgs = bytes()

    def on_SIGUSR1(self):
        print("sniffer got SIGUSR1")
        showScreen(self.screen)

    def purge_server_message(self):
        if len(self.server_msgs):
            self.stream.feed(self.server_msgs.decode("big5uao", 'replace'))
            locate_from_screen(self.screen, bBottom=True)
            self.server_msgs = bytes()

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

        sESC = '\x1b'
        sCSI = '['
        sNUM = '0'

        state = None
        number = ""
        for b in content:
            if state != sNUM:
                number = ""

            c = chr(b)
            if state == None:
                if c == sESC:
                    state = sESC
                elif c == '\r':
                    print("Key: Enter")
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    print("xterm key:", self.xterm_keys[b - ord('A')])
                elif '0' <= c <= '9':
                    state = sNUM
                    number += c
                    continue
                state = None
            elif state == sNUM:
                if '0' <= c <= '9':
                    number += c
                    continue
                elif c == '~':
                    number = int(number)
                    if 1 <= number <= len(self.vt_keys):
                        print("vt key:", self.vt_keys[number-1])
                state = None

    async def server_msg_timeout(self, flow, event):

        print("server_msg_timeout() started, socket opened:", (flow.websocket.timestamp_end is None))
        while flow.websocket.timestamp_end is None:
            await event.wait()

            rcv_len = len(self.server_msgs)
            while True:
                await asyncio.sleep(0.1)
                if len(self.server_msgs) <= rcv_len:
                    break

            print("Server event timeout! Pending:", len(self.server_msgs))
            if event.is_set():
                event.clear()
                self.purge_server_message()

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
        self.screen.reset()
        self.stream.reset()
        self.lastServerMsgTime = -1
        self.server_msgs = bytes()
        self.server_event = asyncio.Event()
        asyncio.create_task(self.server_msg_timeout(flow, self.server_event))

    def websocket_message(self, flow: http.HTTPFlow):
        """

            Called when a WebSocket message is received from the client or

            server. The most recent message will be flow.messages[-1]. The

            message is user-modifiable. Currently there are two types of

            messages, corresponding to the BINARY and TEXT frame types.

        """

        if flow.websocket:

            flow_msg = flow.websocket.messages[-1]

            '''
            if self.lastServerMsgTime < 0:
                self.lastServerMsgTime = flow_msg.timestamp

            deltaTime = flow_msg.timestamp - self.lastServerMsgTime
            print("\nMessages: %i (%f @%f)" % (len(flow.websocket.messages), flow_msg.timestamp, deltaTime))
            self.lastServerMsgTime = flow_msg.timestamp

            dir = "S>" if flow_msg.from_client else "R<"
            type = "T" if flow_msg.is_text else "B"
            l = len(flow_msg.text) if flow_msg.is_text else len(flow_msg.content)

            print(dir + "[" + type + "] len %i" % l)
            '''

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

addons = [
    SniffWebSocket()
]

