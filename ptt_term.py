import sys
import os
import re
import pyte
import asyncio
import time
import socket
import traceback

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


class PttTerm:

    class _State:
        Unknown = 0
        Waiting = 1
        InPanel = 2
        InBoard = 3
        InThread = 4

    persistor = PttPersist()

    def __init__(self, columns, lines):
        self.reset()

        self.screen = MyScreen(columns, lines)
        # self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)

        self.thread = PttThread()

        if not self.persistor.is_connected():
            self.persistor.connect()
        print("persistor:", self.persistor.is_connected())

    def reset(self):
        self.flow = None
        self.read_flow = False
        self.state = self._State.Unknown
        self.event = UserEvent.Unknown
        if hasattr(self, "thread"):
            self.thread.clear()
        if hasattr(self, "macro_task") and not self.macro_task.done():
            self.macro_task.cancel()
            del self.macro_task

    def resize(self, columns, lines):
        self.screen.resize(lines, columns)

    def cursor_up(self):
        self.screen.cursor_up()

    def cursor_down(self):
        self.screen.cursor_down()

    def feed(self, data: bytes):
        self.stream.feed(data.decode("big5uao", 'replace'))

    def flowStarted(self, flow, from_file: bool):
        self.flow = flow    # ptt_proxy.ProxyFlow
        self.read_flow = from_file

        # if flow is read from file, don't persist
        self.thread.setPersistentState(not from_file)

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
        {'data': b'\x1a', 'state': [_State.InPanel, _State.InBoard]},   # Ctrl-Z
        {'data': b'b',    'state': [_State.InPanel, _State.InBoard]},   # will send to the board SYSOP if no board is viewed previously
        {'data': b' ', 'state': _State.InBoard, 'timeout': True},       # skips the onboarding screen or allows timeout
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

        self.thread.setPersistentState(False)
        self.macro_event = asyncio.Event()
        self.macro_task = asyncio.create_task(self.run_macro(self.macros_pmore_config, self.macro_event))

    def persistThread(self, thread):
        if not self.persistor.is_connected():
            self.persistor.connect()
        if self.persistor.is_connected():
            self.persistor.send(PttPersist.TYPE_THREAD, thread)

    def pre_refresh(self):
        if self.state == self._State.InBoard and self.event in [UserEvent.Key_Right, UserEvent.Key_Enter]:
            showCursor(self.screen)     # entering a thread

        if self.state == self._State.InThread and self.thread.isSwitchEvent(self.event):
            self.thread.switch(self.persistThread)

    def post_refresh(self):
        newState = self._refresh()

        if newState in [self._State.Waiting, self._State.Unknown]:
            # TODO: screen already changed but state remains
            if self.state == self._State.InThread:
                self.thread.setWaitingState(True)
            if hasattr(self, "macro_task") and not self.macro_task.done():
                self.macro_event.set()
            return
        else:
            if self.state == self._State.InThread:
                self.thread.setWaitingState(False)

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

            updateScreen, lastRow = self.thread.view(self.screen.display[0:-1], firstLine, lastLine, percent == 100)
            if updateScreen: self.updateScreen(firstLine, lastLine, lastRow)

            return self._State.InThread

        return self._State.Unknown

    def updateScreen(self, firstLine, lastLine, lastRow):
        minColumns = 86
        maxWidth = 5
        if self.screen.columns < minColumns: return

        def floorStr(floor):
            if floor:
                return "{:^{width}}".format(floor, width=maxWidth)
            else:
                return ' ' * maxWidth

        data = b''
        width = 0
        for i in range(lastLine, firstLine-1, -1):
            floor = self.thread.floor(i)
            if floor is None: continue
            row = lastRow - (lastLine - i)
            col = minColumns + 1 - maxWidth
            floor = floorStr(floor)
            print(f"update [{row:2}, {col:2}] = '{floor}'")
            data += (b'\x1b[%d;%dH' % (row, col)) + floor.encode()

        # restore cursor position
        data += (b'\x1b[%d;%dH' % (self.screen.cursor.y + 1, self.screen.cursor.x + 1))

        self.flow.sendToClient(data)

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
                except Exception:
                    traceback.print_exc()
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
                traceback.print_exc()

            if event.is_set():
                event.clear()
                try:
                    next = self.handle_macro_event(macro, timeout, prio_data is not None)
                except Exception as e:
                    traceback.print_exc()
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
            self.thread.setPersistentState(True)

        print("run_macro task finished!")

pushedTerm = None

def push(term):
    global pushedTerm
    pushedTerm = term
    pushedTerm.persistor.close()

def pop(term):
    global pushedTerm
    if pushedTerm:
        term.screen = pushedTerm.screen
        term.stream = pushedTerm.stream
        term.flow   = pushedTerm.flow
        term.read_flow = pushedTerm.read_flow
        term.post_refresh()

        pushedTerm = None

