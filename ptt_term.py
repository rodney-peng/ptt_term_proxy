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
        super().draw(char)

        # the cursor will not be at the last column, won't it?
        #     assert self.cursor.x < self.columns
        if ord(char) > 0xff:
            super().draw('')


# for event debugging
class MyDebugStream(pyte.DebugStream):

    def feed(self, chars):
        # DebugStream inherits ByteStream and feed() takes bytes but not string
        # re-route to Stream.feed()
        super(pyte.ByteStream, self).feed(chars)


class PttTerm:
    '''
        This is a virtual terminal reflects client's screen however not absolutely synchronized.
        It can run macros in which inputs are sent to the server on the behavior of client.
        Floor number is inserted to messages from the server and only appears on a client's screen.
        Client's message may be altered or dropped depending on feature or desire.

        client <-- proxy --> server
                     |
                   PttTerm (virtual terminal)
    '''

    Unknown = 0
    Waiting = 1
    InPanel = 2
    InBoard = 3
    InThread = 4

    # substates
    waitingURL     = 1
    waitingRefresh = 2

    class _State:

        def __init__(self, state = 0, substate = 0):
            self.state = state
            self.substate = substate

        def __repr__(self):
            return str(self.state) if self.substate == 0 else f"{self.state}.{self.substate}"

        def __eq__(self, other):
            if isinstance(other, self.__class__):
                return self.state == other.state and \
                       (self.substate == 0 or other.substate == 0 or self.substate == other.substate)
            elif isinstance(other, int):
                return self.state == other
            else:
                raise AssertionError(f"{other} is neither PttTerm._State nor integer.")

        def __ne__(self, other):
            return not self.__eq__(other)

        # If there is only one state to check, keyword 'is' also works
        def is_exact(self, *others):
            for other in others:
                if isinstance(other, self.__class__) and self.state == other.state and self.substate == other.substate:
                    return True
            return False

    _State.Unknown = _State(Unknown)
    _State.Waiting = _State(Waiting)
    _State.InPanel = _State(InPanel)
    _State.InBoard = _State(InBoard)
    _State.InBoardWaitingURL     = _State(InBoard, waitingURL)
    _State.InBoardWaitingRefresh = _State(InBoard, waitingRefresh)
    _State.InThread = _State(InThread)

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
        self.state = self._State()
        self.autoURL = True    # get URL/AIDC automatically when starts reading a thread
        self.threadLine = None
        self.threadURL = None

        self.threadUpdated = None
        if hasattr(self, "thread"):
            self.thread.clear()

        self._userEvent = UserEvent.Unknown
        if self.isMacroRunning():
            self.macro_task.cancel()
            del self.macro_task

    def reload(self, retired):
        print("PttTerm.reload()!")
        retired.persistor.close()

        # copying attributes from __dict__ only works if all attributes are system-defined objects
        #vars(self).update(vars(retired))

        # it's assumed the class of screen and stream are not changed.
        self.screen = retired.screen
        self.stream = retired.stream

        # just assign self.thread to retired.thread is insufficient for reloading.
        # this is why ptt_thread.py seems not being reloaded.
        #self.thread = retired.thread

        self.thread.reload(retired.thread)

    def showScreen(self):
        self.showCursor(False)
        lines = self.screen.display
        for n, line in enumerate(lines, 1):
            print("%2d" % n, "'%s'" % line)

    def showCursor(self, lineAtCursor=True):
        print("Cursor:", self.screen.cursor.y + 1, self.screen.cursor.x + 1, end = " ")
        if lineAtCursor:
            print("'%s'" % self.screen.display[self.screen.cursor.y])
        else:
            print("lines: %d" % self.screen.lines)

    def resize(self, columns, lines):
        self.screen.resize(lines, columns)

    def cursor_up(self):
        self.screen.cursor_up()

    def cursor_down(self):
        self.screen.cursor_down()

    def feed(self, data: bytes):
        self.stream.feed(data.decode("big5uao", 'replace'))

    def flowStarted(self, flow, from_file: bool):
        self.flow = flow    # ptt_proxy.websocket_message.ProxyFlow
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

    def isMacroRunning(self):
        return hasattr(self, "macro_task") and not self.macro_task.done()

    macros_pmore_config = [
        #{'data': b' ', 'state': _State.Unknown},  # if starts with onboarding screen once logged in
        {'data': b'\x1a', 'state': [_State.InPanel, _State.InBoard]},   # Ctrl-Z
        # will send to the board SYSOP if no board is viewed previously
        {'data': b'b',    'state': [_State.InPanel, _State.InBoard]},
        {'data': b' ',    'state': _State.InBoard, 'timeout': True},    # skips the onboarding screen or allows timeout
        # reads the thread at cursor or retry after cursor Up
        # b'r' will not trigger autoURL in contrary to Enter(b'\r') and Right(b'\x1b[C')
        {'data': b'r',   'state': [_State.InBoard, _State.InThread], 'timeout': b'\x1b[A\x1b[A\x1b[A', 'retry': 5},
        {'data': b'o',   'state': _State.InThread},   # enters thread browser config
        {'data': b'm',   'state': _State.InThread, 'row': -5, 'pattern': '\*顯示', 'retry': 3}, # 斷行符號: 顯示
        {'data': b'l',   'state': _State.InThread, 'row': -4, 'pattern': '\*無', 'retry': 3},   # 文章標頭分隔線: 無
        {'data': b' ',   'state': _State.InThread},    # ends config
        {'data': b'\x1b[D', 'state': _State.InBoard},   # Left and leaves the thread
        {'data': b'\x1a',   'state': _State.InBoard},   # Ctrl-Z
        {'data': b'c',      'state': _State.InPanel},   # goes to 分類看板
        {'data': b'\x1b[D', 'state': _State.InPanel}    # Left and goes to 主功能表
        ]
    def runPmoreConfig(self):

        def doneHook(macros):
            print("runPmoreConfig done!")
            self.thread.setPersistentState(True)
            self.autoURL = True

        if self.isMacroRunning():
            self.macro_task.cancel()
            while not self.macro_task.done():
                time.sleep(0.1)

        # disabling persistence depends on macro, so it's not disabled by default
        self.thread.setPersistentState(False)
        self.autoURL = False

        self.macro_event = asyncio.Event()
        self.macro_task = asyncio.create_task(self.run_macro(self.macros_pmore_config, self.macro_event, doneHook))

    def persistThread(self, thread):
        if not self.persistor.is_connected():
            self.persistor.connect()
        if self.persistor.is_connected():
            self.persistor.send(PttPersist.TYPE_THREAD, thread)

    # before the first segment is sent to the client
    def pre_update(self):
        if self.state == self._State.InThread and self.threadUpdated and \
           (self.thread.isUpdateEvent(self._userEvent) or self.thread.isSwitchEvent(self._userEvent)):
            self.updateThread(*self.threadUpdated, True)
            self.threadUpdated = None

    def _threadLine(self, line=0):
        if line == 0:
            line = self.screen.cursor.y
        elif 1 <= line <= self.screen.lines:
            line -= 1
        else:
            raise AssertionError(f"Line {line} is out of range 1~{self.screen.lines}")

        return self.screen.display[line].lstrip(" >").rstrip()
        '''
        try:
            # how about '★' sticky threads?
            number = re.match("[\s>]\s*?([0-9]+)", line).group(1)
        except (AttributeError, IndexError):
            return 0
        else:
            number = int(number)
            print(number, "'%s'" % line)
            return number
        '''

    def isThreadDeleted(self, line=0):
        if line == 0:
            line = self.screen.cursor.y
        elif 1 <= line <= self.screen.lines:
            line -= 1
        else:
            raise AssertionError(f"Line {line} is out of range 1~{self.screen.lines}")

        line = self.screen.display[line].strip()
        # (本文已被刪除) or (已被xxx刪除)
        deleted = re.search("-            □ (.*已被.*刪除)", line) is not None
        print(deleted, "'%s'" % line)
        return deleted

    # before the screen is updated, some segments have already been sent to the client
    def pre_refresh(self):
        print("pre_refresh:", self.state, UserEvent.name(self._userEvent))
        # "== self._State.InBoard" doesn't work here
        if self.state is self._State.InBoard and self.isThreadEnteringEvent(self._userEvent, True):
            # entering a thread
            if self.threadURL:
                print("Set URL:", self.threadURL, "'%s'" % self.threadLine)
                self.thread.setURL(self.threadURL)

        if self.state == self._State.InThread and self.thread.isSwitchEvent(self._userEvent):
            # left a thread
            self.thread.switch(self.persistThread)
            # don't clear self.threadURL until cursor is moved

    def scanURL(self):
        url = None
        lines = self.screen.display
        for i in range(2, self.screen.lines - 4):   # the box spans at least 4 lines
            if lines[i  ].startswith("│ 文章代碼(AID):") and \
               lines[i+1].startswith("│ 文章網址:"):

                url = (lines[i+1])[7:].strip(" │")
                board_fn = PttThread.url2fn(url)
                _aidc = re.match("\ *?#([0-9A-Za-z-_]{8})", (lines[i])[12:])

                if board_fn and _aidc and \
                   PttThread.fn2aidc(board_fn[1]) == _aidc.group(1):
                    break
                else:
                    url = None
        return url


    # after the screen is updated
    def post_refresh(self):
        newState = self._refresh()

        if newState in [self._State.Waiting, self._State.Unknown]:
            if self.state is self._State.InBoardWaitingURL:
                self.threadURL = self.scanURL()
                if newState == self._State.Waiting:
                    self.flow.sendToServer(b' ')    # escape from waiting
                    self.state = self._State.InBoardWaitingRefresh
                else:
                    self.state = self._State.InBoard
            elif self.state == self._State.InThread:
                self.thread.setWaitingState(True)

            if self.isMacroRunning(): self.macro_event.set()
            return
        else:
            if self.state == self._State.InThread:
                self.thread.setWaitingState(False)

        prevState = self.state
        self.state = newState

        if prevState.is_exact(self._State.InBoardWaitingURL, self._State.InBoardWaitingRefresh) and \
           newState is self._State.InBoard:
            if self.threadURL:
                # to enter the thread, send 'r' to skip replacement of 'Q' again
                self.flow.sendToServer(b'r')

        # out of a thread and not caught by self.thread.isSwitchEvent() in pre_refresh()
        # this is necessary because user can search and jump to board while viewing thread
        if prevState == self._State.InThread and newState != self._State.InThread:
            self.threadUpdated = None
            self.thread.switch(self.persistThread)

        # left a board or returned to board from a different thread
        if (prevState == self._State.InBoard and \
            newState not in [self._State.InBoard, self._State.InThread]) or \
           (prevState == self._State.InThread and newState == self._State.InBoard and \
            self.threadLine != self._threadLine()):
            self.threadLine = None
            self.threadURL = None

        # if flow is read from file, don't run macro
        if not self.read_flow and not hasattr(self, "macro_task"):
            if prevState == self._State.Unknown and newState == self._State.InPanel:
                self.runPmoreConfig()
        elif self.isMacroRunning():
            self.macro_event.set()

    def _refresh(self):
        lines = self.screen.display

        for input_pattern in [".+請?按.+鍵.*繼續", "請選擇", '搜尋.+', '\s*★快速切換', '\s*跳至第幾項:']:
            if re.match(input_pattern, lines[-1]):
                print("Waiting input...")
                return self._State.Waiting

        for panel in ['【主功能表】', '【分類看板】', '【看板列表】', '【 選擇看板 】', '【個人設定】']:
            if re.match(panel, lines[0]):
                print("In panel:", panel)
                return self._State.InPanel

        # a regex for board name should be "[\w-]+"

        if re.match("\s*文章選讀", lines[-1]):
            try:
                # In '系列' only displays the first thread for a series
                board = re.search("^\s*【(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》\s*$", lines[0]).group(3)
                print("In board: '%s'" % board)
            except (AttributeError, IndexError):
                print("Board missing: '%s'" % lines[0])
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
                    board = re.match("\s+作者\s+.+看板\s+([\w-]+)\s*$", lines[0]).group(1)
#                    print("Board: '%s'" % board)
                except (AttributeError, IndexError):
                    print("Board missing: '%s'" % lines[0])
                try:
                    title = re.match("\s+標題\s+(\S.+)\s*$", lines[1]).group(1)
#                    print("Title: '%s'" % title)
                except (AttributeError, IndexError):
                    print("Title missing: '%s'" % lines[1])

            updateThread, lastRow = self.thread.view(self.screen.display[0:-1], firstLine, lastLine, percent == 100)
            if updateThread:
                self.threadUpdated = (firstLine, lastLine, lastRow)
                self.updateThread(*self.threadUpdated)
                # floor numbers will be cleared in pre_update()

            return self._State.InThread

        return self._State.Unknown

    def updateThread(self, firstLine, lastLine, lastRow, clear=False):
        minColumns = 86
        maxWidth = 5
        if self.screen.columns < minColumns: return

        def floorStr(floor):
            if floor and not clear:
                return "{:^{width}}".format(floor, width=maxWidth)
            else:
                return ' ' * maxWidth

        data = b''
        width = 0
        for i in range(lastLine, firstLine-1, -1):
            floor = self.thread.floor(i)
            if (floor is None) or (clear and floor == 0): continue
            row = lastRow - (lastLine - i)
            col = minColumns + 1 - maxWidth
            floor = floorStr(floor)
#            print(f"update [{row:2}, {col:2}] = '{floor}'")
            data += (b'\x1b[%d;%dH' % (row, col)) + floor.encode()

        # restore cursor position
        data += (b'\x1b[%d;%dH' % (self.screen.cursor.y + 1, self.screen.cursor.x + 1))

        if clear:
            self.flow.insertToClient(data)
        else:
            self.flow.sendToClient(data)

    @staticmethod
    def isCursorMovingEvent(event: UserEvent):
        return event in [UserEvent.Key_Up, UserEvent.Key_Down, UserEvent.Key_PgUp, UserEvent.Key_PgDn,
                         UserEvent.Key_Home, UserEvent.Key_End, UserEvent.Ctrl_B, UserEvent.Ctrl_F,
                         # leaving a board
                         UserEvent.Key_Left] or \
               chr(event) in "pknjPN0$=[]<>-+S{}123456789q"     # 'q' as well

    @staticmethod
    def isThreadEnteringEvent(event: UserEvent, include_r=False):
        return event in [UserEvent.Key_Right, UserEvent.Key_Enter] or \
               (include_r and event == UserEvent.r)

    # the client message will be dropped if false is returned
    # the current user event will be replaced if a bytes object is returned
    def userEvent(self, event: UserEvent, uncommitted = False):
        print("User event:", UserEvent.name(event))

        # most often event first

        if event != UserEvent.Unknown and self.state == self._State.InThread:
            if self.thread.is_prohibited(event):
                return False

        if self.autoURL and (self.state is self._State.InBoard):
            if self.isCursorMovingEvent(event):
                print("Clear URL:", self.threadURL, "'%s'" % self.threadLine)
                self.threadLine = None
                self.threadURL = None
            elif self.isThreadEnteringEvent(event) and \
                 self.threadLine is None and not self.isThreadDeleted():
                # replace event with 'Q' for getting the URL
                self.state = self._State.InBoardWaitingURL
                self.threadLine = self._threadLine()
                self._userEvent = event
                return b"Q"

        if uncommitted:
            if event == UserEvent.Key_Up:
                self.cursor_up()
            elif event == UserEvent.Key_Down:
                self.cursor_down()

        self._userEvent = event
        return event

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
    async def run_macro(self, macros, event, doneHook=None):
        print("run_macro task started!")
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

        if doneHook: doneHook(macros)
        print("run_macro task finished!")

if __name__ == "__main__":
    PttTerm(128, 32)

