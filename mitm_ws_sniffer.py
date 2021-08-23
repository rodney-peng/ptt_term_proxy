from mitmproxy import addonmanager, http, log, tcp, websocket

from uao import register_uao
register_uao()

import sys
import os
import re
import pyte

# fix for double-byte character positioning and drawing
class MyScreen(pyte.Screen):

    def cursor_position(self, line=None, column=None):
        line = (line or 1) - 1
        column = (column or 1) - 1
        if line or column:
            # double-bytes character should be treated as width 2
            content = self.display[line]
            chridx = 0  # character index
            curpos = 0  # cursor position
            while curpos < column:
                curpos += (1 if (len(content) <= chridx) or (ord(content[chridx]) < 256) else 2)
                chridx += 1
#            if chridx != curpos:
#                print("col", column, "cur", curpos, "idx", chridx, "|%s|" % content)
            super(MyScreen, self).cursor_position(line + 1, chridx + 1)
        else:
            super(MyScreen, self).cursor_position()

    def draw(self, char):
        content = self.display[self.cursor.y]
        if ord(char) < 256:
            # a single-byte character overwrites a double-byte character
            bInsert = False
            if ord(content[self.cursor.x]) >= 256:
#                print("'%c' overwrites '%c'" % (char, content[self.cursor.x]))
                bInsert = True
            super(MyScreen, self).draw(char)
            if bInsert:
                self.insert_characters()
        else:
            # a double-byte character overwrites two characters
            bDelete = False
            bErase = False
            try:
                # self.cursor.x+1 may be out of boundary
                if ord(content[self.cursor.x]) < 256:
#                    print("'%c' overwrites '%c' '%c'" % (char, content[self.cursor.x], content[self.cursor.x+1]))
                    if ord(content[self.cursor.x+1]) < 256:
                        # two single-byte characters
                        bDelete = True
                    else:
                        # a single-byte character followed by a double-byte character
                        bErase = True
            except:
                pass
            if bDelete:
                self.delete_characters()
            elif bErase:
                self.erase_characters(2)
            super(MyScreen, self).draw(char)

# for event debugging
class MyStream(pyte.Stream):
    def __init__(self, to=sys.stdout, only=(), *args, **kwargs):
        super(MyStream, self).__init__(*args, **kwargs)

        def safe_str(chunk):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            elif not isinstance(chunk, str):
                chunk = str(chunk)

            return chunk

        class Bugger(object):
            __before__ = __after__ = lambda *args: None

            def __getattr__(self, event):
                def inner(*args, **flags):
                    to.write(event.upper() + " ")
                    to.write("; ".join(map(safe_str, args)))
                    to.write(" ")
                    to.write(", ".join("{0}: {1}".format(name, safe_str(arg))
                                       for name, arg in flags.items()))
                    to.write(os.linesep)
                return inner

        self.attach(Bugger(), only=only)


def displayScreen(screen):
    lines = screen.display
    print("Cur %i %i lines %i" % (screen.cursor.y, screen.cursor.x, len(lines)))
    for n, line in enumerate(lines, 1):
        print(n, line)

def navigate(screen, content):
    print(content)
    sESC = ord('\x1b')
    sCSI = ord('[')
    sNUM = ord('0')

    state = None
    number = ""
    for b in content:
        if state != sNUM:
            number = ""

        if state == None:
            if b == sESC:
                state = sESC
        elif state == sESC:
            if b == sCSI:
                state = sCSI
            else:
                state = None
        elif state == sCSI:
            if b == ord('A'):
                print("cursor up")
                screen.cursor_up()
            elif b == ord('B'):
                print("cursor down")
                screen.cursor_down()
            elif b == ord('C'):
                print("Key Right")
            elif b == ord('D'):
                print("Key Left")
            elif b == ord('F'):
                print("Key End")
            elif b == ord('H'):
                print("Key Home")
            elif ord('0') <= b <= ord('9'):
                state = sNUM
                number += chr(b)
                continue
            state = None
        elif state == sNUM:
            if ord('0') <= b <= ord('9'):
                number += chr(b)
                continue
            elif b == ord('~'):
                print("vtseq", number)
                number = int(number)
                if number == 1:
                    print("key Home")
                elif number == 2:
                    print("key Insert")
                elif number == 3:
                    print("key Delete")
                elif number == 4:
                    print("key End")
                elif number == 5:
                    print("key PgUp")
                elif number == 6:
                    print("key PgDn")
                elif number == 7:
                    print("key Home")
                elif number == 8:
                    print("key End")
            state = None

def locate(screen, bTop = True, bBottom = False):
    lines = screen.display
    y = screen.cursor.y
    x = screen.cursor.x

    if bBottom:
        print("bottom:", lines[-1], "|||")
        if re.search("請按.+鍵.*繼續", lines[-1]):
            print("等待按鍵")
            return

        if re.search("請選擇", lines[-1]):
            print("等待選擇")
            return

        displayScreen(screen)

    if bTop:
        print("top   :", lines[0], "|||")
        if re.match("\s*【板主:", lines[0]):
            try:
                board = re.search("看板《(\w+)》\s*$", lines[0]).group(1)
            except:
                print("Board missing:", lines[0], "|||")
                return

            print("In board:", board)
            print("pos:", y, x, lines[y], "|||")
            return

        board = re.match("\s+作者\s+.+看板\s+(\w+)\s*$", lines[0])
        if board:
            print("Board:", board.group(1))
            try:
                title = re.match("\s+標題\s+(\S.+)\s*$", lines[1]).group(1)
            except:
                print("Title missing:", lines[1], "|||")
                return

            print("Title:", title)
            return

class SniffWebSocket:

    def __init__(self):
        self.screen = MyScreen(128, 32)
#        self.stream = MyStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)
        pass

    # Websocket lifecycle

    def websocket_handshake(self, flow: http.HTTPFlow):
        """

            Called when a client wants to establish a WebSocket connection. The

            WebSocket-specific headers can be manipulated to alter the

            handshake. The flow object is guaranteed to have a non-None request

            attribute.

        """
        print("websocket_handshake")
        pass

    def websocket_start(self, flow: http.HTTPFlow):
        """

            A websocket connection has commenced.

        """
        print("websocket_start")
        self.screen.reset()
        self.stream.reset()
        self.lastServerMsgTime = -1
        pass

    def websocket_message(self, flow: http.HTTPFlow):
        """

            Called when a WebSocket message is received from the client or

            server. The most recent message will be flow.messages[-1]. The

            message is user-modifiable. Currently there are two types of

            messages, corresponding to the BINARY and TEXT frame types.

        """

        if flow.websocket:

            flow_msg = flow.websocket.messages[-1]

            if self.lastServerMsgTime < 0:
                self.lastServerMsgTime = flow_msg.timestamp

            deltaTime = flow_msg.timestamp - self.lastServerMsgTime
            print("\nMessages: %i (%f @%f)" % (len(flow.websocket.messages), flow_msg.timestamp, deltaTime))
            self.lastServerMsgTime = flow_msg.timestamp

            dir = "S>" if flow_msg.from_client else "R<"
            type = "T" if flow_msg.is_text else "B"
            l = len(flow_msg.text) if flow_msg.is_text else len(flow_msg.content)

            print(dir + "[" + type + "] len %i" % l)

            if not flow_msg.from_client:
#                print("\n", flow_msg.content)
                self.stream.feed(flow_msg.content.decode("big5uao", 'replace'))
                print("Cursor=", self.screen.cursor.y, self.screen.cursor.x)

                # dirty trick to identify the last segment with size
                # (FIXME)
                # but sometimes a segment with size 1021 is not the last or the last segment is larger than 1021
                # (FIXME)
                # a double-byte character could be split into two segments
                if len(flow_msg.content) < 1021:
                    locate(self.screen, bBottom=True)
            else:
                navigate(self.screen, flow_msg.content)


    def websocket_error(self, flow: http.HTTPFlow):
        """

            A websocket connection has had an error.

        """
        print("websocket_error, %r" % flow)
        pass

    def websocket_end(self, flow: http.HTTPFlow):
        """

            A websocket connection has ended.

        """
        print("websocket_end")
        pass

addons = [
    SniffWebSocket()
]

