import sys
import os
import re
import pyte
import asyncio
import time
import socket
import traceback
import inspect
from dataclasses import dataclass

from uao import register_uao
register_uao()

from ptt_event import ClientEvent, ProxyEvent
from ptt_menu import PttMenu, QuickSwitch, SearchBoard, SearchBox, HelpScreen, JumpToEntry
from ptt_board import PttBoard

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


class BoardList(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool(lines[0].lstrip().startswith("【看板列表】") and lines[-1].lstrip().startswith("選擇看板"))

    def reset(self):
        super().reset()
        self.boards = {}

    def add(self, name, board):
        self.boards[name] = board

    def is_empty(self):
        return len(self.boards) == 0

    def enter(self, y, x, lines):
        yield from super().enter(y, x, lines)
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), lines[y])
        else:
            self.cursorLine = ""

    def pre_update_self(self, y, x, lines):
        self.cursorLine = lines[y]
        yield from super().pre_update_self(y, x, lines)

    subMenus = { ClientEvent.Ctrl_Z: QuickSwitch,
                 ClientEvent.Ctrl_S: SearchBoard,
                 ClientEvent.s: SearchBoard,
                 ClientEvent.h: HelpScreen,
                 ClientEvent.Slash: SearchBox,
                 ClientEvent.Enter:     PttBoard,
                 ClientEvent.Key_Right: PttBoard,
                 ClientEvent.l:         PttBoard,
                 ClientEvent.r:         PttBoard,
                 ClientEvent.Key1: JumpToEntry,
                 ClientEvent.Key2: JumpToEntry,
                 ClientEvent.Key3: JumpToEntry,
                 ClientEvent.Key4: JumpToEntry,
                 ClientEvent.Key5: JumpToEntry,
                 ClientEvent.Key6: JumpToEntry,
                 ClientEvent.Key7: JumpToEntry,
                 ClientEvent.Key8: JumpToEntry,
                 ClientEvent.Key9: JumpToEntry,
               }

    def isSubMenuEntered(self, menu, lines):
        if menu is PttBoard:
            print(self.prefix(), "isSubMenuEntered", lines[-1])
            if "請按任意鍵繼續" in lines[-1] or "動畫播放中" in lines[-1]:
                print(self.prefix(), "isSubMenuEntered", self.cursorLine)
                board = re.match("[> ]+[0-9]+[ ˇ]+([\w-]+)", self.cursorLine)
                if board: board = board.group(1)
            else:
                board = ProxyEvent.eval_type(menu.is_entered(lines), ProxyEvent.BOARD_NAME)
            if board:
                if board not in self.boards:
                    self.boards[board] = PttBoard(board)
                self.subMenu = self.boards[board]
            yield ProxyEvent.as_bool(board is not None)
        else:
            yield from super().isSubMenuEntered(menu, lines)

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), "post_update_self", lines[y])
        if False: yield

@dataclass
class NotificationRendition:
    width: int = 0
    center: bool = False
    blink: bool = False
    fg: str = None  # pyte.graphics.FG
    bg: str = None  # pyte.graphics.BG


class PttTerminal:

    def __init__(self, columns, lines):
        self.screen = MyScreen(columns, lines)
        # self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)

        self.reset()

    def reset(self):
        self.clientEvent = ClientEvent.Unknown
        self.menu = None    # BoardList or PttBoard
        self.boardlist = BoardList()

    # screen and stream operations

    def resize(self, columns, lines):
        self.screen.resize(lines, columns)

    def cursor_up(self):
        self.screen.cursor_up()

    def cursor_down(self):
        self.screen.cursor_down()

    def feed(self, data: bytes):
        self.stream.feed(data.decode("big5uao", 'replace'))

    def showScreen(self):
        self.showCursor(False)
        lines = self.screen.display
        for n, line in enumerate(lines, 1):
            print("%2d" % n, "'%s'" % line)

    def cursor(self, strip=True):
        line = self.screen.display[self.screen.cursor.y]
        return (self.screen.cursor.y, self.screen.cursor.x,
                # rstrip() only, preserves leading spaces (and cursor)
                line.rstrip() if strip else line)

    def showCursor(self, lineAtCursor=True):
        y, x, line = self.cursor(False)
        print("Cursor:", y, x, end = " ")
        if lineAtCursor:
            print("'%s'" % line)
        else:
            print("lines: %d" % self.screen.lines)

    def cursor_position(self, y = None, x = None):
        if y is None:
            y = self.screen.cursor.y
        elif y < 0:
            y = self.screen.lines + y
        if x is None:
            x = self.screen.cursor.x
        elif x < 0:
            x = self.screen.columns + x

        return b'\x1b[%d;%dH' % (y + 1, x + 1)

    def selectGraphic(self, y = None, x = None, rendition: NotificationRendition = None):
        if y is None or x is None:
            return b'\x1b[0m'

        if rendition and rendition.fg:
            fg = rendition.fg
        else:
            fg = self.screen.buffer[y][x].fg
        if rendition and rendition.bg:
            bg = rendition.bg
        else:
            bg = self.screen.buffer[y][x].bg

        def keyByValue(_dict, value):
            return list(_dict.keys())[list(_dict.values()).index(value)]

        blink = rendition.blink if rendition else False

        fgcode = keyByValue(pyte.graphics.FG, fg)
        bgcode = keyByValue(pyte.graphics.BG, bg)
        return b"\x1b[" + (b'5;' if blink else b'') + b"%d;%dm" % (fgcode, bgcode)

    def draw(self, row, col, content):
        return (b'\x1b[%d;%dH' % (row, col)) + content.encode("big5uao", "replace")

    # messages from proxy

    vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
    xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
    def client_message(self, content):
        print("\nclient:", content)

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
        replace = b''
        replaced = False
        while n < len(content):
            handler = None
            b = content[n]

            if state != sNUM: number = ""

            c = chr(b)
            if state == None:
                cmdBegin = n
                if c == sESC:
                    state = sESC
                elif b == IAC:
                    state = IAC
                else:
                    handler = self.client_event(b)
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    if c == 'A':
                        handler = self.client_event(ClientEvent.Key_Up, uncommitted)
                    elif c == 'B':
                        handler = self.client_event(ClientEvent.Key_Down, uncommitted)
                    elif c == 'C':
                        handler = self.client_event(ClientEvent.Key_Right)
                    elif c == 'D':
                        handler = self.client_event(ClientEvent.Key_Left)
                    elif c == 'F':
                        handler = self.client_event(ClientEvent.Key_End)
                    elif c == 'H':
                        handler = self.client_event(ClientEvent.Key_Home)
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
                        handler = self.client_event(ClientEvent.Key_PgUp)
                    elif number == 6:
                        handler = self.client_event(ClientEvent.Key_PgDn)
                    elif number in [1, 7]:
                        handler = self.client_event(ClientEvent.Key_Home)
                    elif number in [4, 8]:
                        handler = self.client_event(ClientEvent.Key_End)
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
                        self.resize(width, height)
                        n += 4
                        state = None
                    else:
                        break
                elif 0 <= b <= 3:
                    state = None
                else:
                    break

            if handler:    # a generator
                assert inspect.isgenerator(handler)
                lets_do_it = handler
                for event in lets_do_it:
                    if event._type == ProxyEvent.DROP_CONTENT:
                        # drop the current input
                        if not replaced: replace = content[:cmdBegin]
                        replaced = True
                    elif event._type == ProxyEvent.REPLACE_CONTENT:
                        # replace the current input
                        if not replaced: replace = content[:cmdBegin]
                        replace += event.content
                        replaced = True
                    else:
                        yield from self.lets_do_terminal_event(lets_do_it, event, True)
            elif replaced and state == None:
                replace += content[cmdBegin:n+1]

            n += 1

        if replaced:
            if replace:
                yield ProxyEvent(ProxyEvent.REPLACE_CONTENT, replace)
            else:
                yield ProxyEvent(ProxyEvent.DROP_CONTENT)

    def client_event(self, event: ClientEvent, uncommitted = False):
        if uncommitted:
            if event == ClientEvent.Key_Up:
                self.cursor_up()
            elif event == ClientEvent.Key_Down:
                self.cursor_down()

        if self.menu:
            yield from self.menu.client_event(event)
        else:
            print("client event:", ClientEvent.name(event))
            self.clientEvent = event

        if False: yield

    def pre_server_message(self):
        if self.menu:
            y, x, line = self.cursor()
            lines = self.screen.display
            lets_do_it = self.menu.pre_update(y, x, lines)
            for event in lets_do_it:
                yield from self.lets_do_terminal_event(lets_do_it, event, True)

        if False: yield

    def post_server_message(self):
        y, x, line = self.cursor()
        lines = self.screen.display
        if self.menu:
            lets_do_it = self.menu.post_update(y, x, lines)
            for event in lets_do_it:
                if event._type == ProxyEvent.RETURN:
                    print("terminal: exit", self.menu)
                    self.menu = None
                else:
                    yield from self.lets_do_terminal_event(lets_do_it, event)
            if self.menu: return

        in_boardlist = ProxyEvent.eval_bool(BoardList.is_entered(lines))
        if in_boardlist:
#            if self.boardlist.is_empty():
#                yield ProxyEvent.run_macro("macros_pmore_config")
            self.menu = self.boardlist
        else:
            board = ProxyEvent.eval_type(PttBoard.is_entered(lines), ProxyEvent.BOARD_NAME)
            if board:
                self.menu = PttBoard(board)
                self.boardlist.add(board, self.menu)

        if self.menu:
            lets_do_it = self.menu.enter(y, x, lines)
            for event in lets_do_it:
                yield from self.lets_do_terminal_event(lets_do_it, event)

    def server_message(self, content):
        print("server: (%d)" % len(content))
        self.feed(content)
        yield from self.post_server_message()

    def lets_do_terminal_event(self, lets_do_it, event: ProxyEvent, pre_update = False):
        if event._type < ProxyEvent.TERMINAL_EVENT:
            yield event
        elif event._type == ProxyEvent.SCREEN_COLUMN:
            lets_do_it.send(self.screen.columns)
        elif event._type == ProxyEvent.CURSOR_BACKGROUND:
            lets_do_it.send(self.screen.buffer[self.screen.cursor.y][self.screen.cursor.x].bg)
        elif event._type == ProxyEvent.DRAW_CLIENT:
            # ptt_event.DrawClient
            draw = event.content
            data = self.draw(draw.row, draw.column, draw.content)
            yield (ProxyEvent.insert_to_client(data) if pre_update else ProxyEvent.send_to_client(data))
        elif event._type == ProxyEvent.DRAW_CURSOR:
            data = self.draw(self.screen.cursor.y + 1, self.screen.cursor.x + 1, '')
            yield (ProxyEvent.insert_to_client(data) if pre_update else ProxyEvent.send_to_client(data))
        else:
            yield ProxyEvent.warning(event)

    # macro support methods

    def currentState(self):
        if self.menu:
            state = type(self.menu)
            menu = self.menu.subMenu
            while menu:
                state = type(menu)
                menu = menu.subMenu
            return state
        else:
            return None

    def verifyState(self, state):
        from collections import abc
        if isinstance(state, abc.Sequence):
            return self.currentState() in state
        else:
            return self.currentState() is state

    def verifyRow(self, row, pattern):
        return re.search(pattern, self.screen.display[row]) is not None

    def lets_do_notifyClient(self, message, rendition: NotificationRendition = None):
        if rendition is None: rendition = NotificationRendition()

        max_width = self.screen.columns if rendition.center else (self.screen.columns // 2)
        rendition.width = min(rendition.width, max_width) if rendition.width else max_width
        message = "{:^{width}}".format(message[:rendition.width], width=rendition.width)

        if rendition.center:
            x = (self.screen.columns // 2) - (len(message) // 2)
        else:
            x = -(self.screen.columns // 4) - (len(message) // 2)

        data  = self.cursor_position(-1, x)
        data += self.selectGraphic(-1, x, rendition)
        data += message.encode("big5uao", "replace")
        data += self.selectGraphic()    # reset color mode
        data += self.cursor_position()  # restore cursor position
        yield ProxyEvent.send_to_client(data)


if __name__ == "__main__":
    PttTerminal(128, 32)

