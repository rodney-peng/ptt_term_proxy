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

from ptt_event import ClientEvent, ProxyEvent, ClientContext
from ptt_boardlist import PttBoardList
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

    def rawData(self, y, x, fg: str = "", bg: str = "", bold: bool = False):
        data = self.buffer[y][x]
#        print("rawData:", y, x, data)
        raw = data.data.encode("big5uao", 'replace')
        if fg != data.fg or bg != data.bg or bold != data.bold:
            fgcode = self.fgCode(data.fg)
            bgcode = self.bgCode(data.bg)
            raw = (b'\x1b[%d;%d;%dm' % (1 if data.bold else 0, fgcode, bgcode)) + raw
        return raw, data.fg, data.bg, data.bold

    def rawDataBlock(self, y, x, length):
        fg = ""
        bg = ""
        bold = False
        data = b''
        for i in range(length):
            raw, fg, bg, bold = self.rawData(y, x + i, fg, bg, bold)
            data += raw
        return data

    @staticmethod
    def fgCode(value):
        _dict = pyte.graphics.FG
        if value == "default": value = "white"
        return list(_dict.keys())[list(_dict.values()).index(value)]

    @staticmethod
    def bgCode(value):
        _dict = pyte.graphics.BG
        if value == "default": value = "black"
        return list(_dict.keys())[list(_dict.values()).index(value)]

# for event debugging
class MyDebugStream(pyte.DebugStream):

    def feed(self, chars):
        # DebugStream inherits ByteStream and feed() takes bytes but not string
        # re-route to Stream.feed()
        super(pyte.ByteStream, self).feed(chars)


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
        self.menu = None    # PttBoardList
        self.boardlist = PttBoardList()
        self.initialized = False

    # screen and stream operations

    def size(self):
        return self.screen.size

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

        blink = rendition.blink if rendition else False

        fgcode = self.screen.fgCode(fg)
        bgcode = self.screen.bgCode(bg)
        return b"\x1b[" + (b'5;' if blink else b'') + b"%d;%dm" % (fgcode, bgcode)

    def draw(self, context: ClientContext):
        row = context.row
        col = context.column
        position = b''
        rendition = b''
        content = context.content.encode("big5uao", "replace")
        if row is not None and col is not None:
            if row < 0: row = self.screen.lines + row + 1
            row = 1 if row < 1 else min(self.screen.lines, row)
            if col < 0: col = self.screen.columns + col + 1
            col = 1 if col < 1 else min(self.screen.columns, col)
            position = b'\x1b[%d;%dH' % (row, col)
            content = content[:self.screen.columns - col + 1]
        if context.fg or context.bg:
            fg = context.fg if context.fg else "white"
            bg = context.bg if context.bg else "black"

            fgcode = self.screen.fgCode(fg)
            bgcode = self.screen.bgCode(bg)
            rendition = b"\x1b[%d;%d;%dm" % (1 if context.bold else 0, fgcode, bgcode)
        return position + rendition + content

    def screenData(self, context: ClientContext):
        row = context.row
        col = context.column
        if row < 0: row = self.screen.lines + row + 1
        row = 1 if row < 1 else min(self.screen.lines, row)
        if col < 0: col = self.screen.columns + col + 1
        col = 1 if col < 1 else min(self.screen.columns, col)
        length = min(context.length, self.screen.columns - col + 1)

        ctx = context
        ctx.row = row
        ctx.column = col
        ctx.content = ""
        ctx.fg = ctx.bg = None

        return self.draw(ctx) + self.screen.rawDataBlock(row - 1, col - 1, length) + self.selectGraphic()

    # messages from proxy

    vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
    xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
    def client_message(self, content):
#        print("terminal.client_message:", content)

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
                        yield from self.lets_do_terminal_event(lets_do_it, event)
            elif replaced and state == None:
                replace += content[cmdBegin:n+1]

            n += 1

        if replaced:
            if replace:
                yield ProxyEvent.replace_content(replace)
            else:
                yield ProxyEvent.drop_content

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
            def exited(menu):
                print("terminal:", menu, "exited")
                self.menu = None
                if False: yield

            lets_do_it1 = self.menu.post_update(y, x, lines)
            lets_do_it2 = self.menu.lets_do_if_return(lets_do_it1, exited(self.menu))
            for event in lets_do_it2:
                yield from self.lets_do_terminal_event(lets_do_it2, event)
            if self.menu: return

        def entered(menu):
            print("terminal:", menu, "entered")
            self.menu = menu
            yield from self.menu.enter(y, x, lines)
            if not self.initialized:
                from ptt_macros import pmore_config
                yield ProxyEvent.run_macro(pmore_config)
                self.initialized = True

        lets_do_it1 = self.boardlist.is_entered(lines)
        lets_do_it2 = self.boardlist.lets_do_if(lets_do_it1, entered(self.boardlist))
        for event in lets_do_it2:
            yield from self.lets_do_terminal_event(lets_do_it2, event)

    def server_message(self, content):
        print("\nterminal.server_message: (%d)" % len(content))
        self.feed(content)
        yield from self.post_server_message()

    def lets_do_terminal_event(self, lets_do_it, event: ProxyEvent, pre_update = False):
        if event._type < ProxyEvent.TERMINAL_EVENT:
            yield event
        elif event._type == ProxyEvent.REQ_SCREEN_COLUMN:
            lets_do_it.send(self.screen.columns)
        elif event._type == ProxyEvent.REQ_CURSOR_BACKGROUND:
            lets_do_it.send(self.screen.buffer[self.screen.cursor.y][self.screen.cursor.x].bg)
        elif event._type == ProxyEvent.REQ_SCREEN_DATA:
            # event.content is a ptt_event.ClientContext object
            data = self.screenData(event.content)
            lets_do_it.send(data)
        elif event._type == ProxyEvent.REQ_SUBMENU_CACHED:
            response = yield event
            reply = lets_do_it.send(response)
            yield reply
        elif event._type == ProxyEvent.DRAW_CLIENT:
            # event.content is a ptt_event.ClientContext object
            data = self.draw(event.content)
            yield (ProxyEvent.insert_to_client(data) if pre_update else ProxyEvent.send_to_client(data))
        elif event._type == ProxyEvent.DRAW_CURSOR:
            data = self.cursor_position()
            yield (ProxyEvent.insert_to_client(data) if pre_update else ProxyEvent.send_to_client(data))
        elif event._type == ProxyEvent.RESET_RENDITION:
            data = self.selectGraphic()
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

    def statistics(self):
        return self.boardlist.statistics()


if __name__ == "__main__":
    PttTerminal(128, 32)

