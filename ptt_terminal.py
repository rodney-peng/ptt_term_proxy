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

from user_event import UserEvent, ProxyEvent
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


class PttTerminal:

    def __init__(self, columns, lines, flow):
        self.reset()

        self.screen = MyScreen(columns, lines)
        # self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)

        self.flow = flow

    def reset(self):
        self.userEvent = UserEvent.Unknown
        self.boards = {}
        self.board = None

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

    def showCursor(self, lineAtCursor=True):
        print("Cursor:", self.screen.cursor.y + 1, self.screen.cursor.x + 1, end = " ")
        if lineAtCursor:
            print("'%s'" % self.screen.display[self.screen.cursor.y])
        else:
            print("lines: %d" % self.screen.lines)

    # messages from proxy and event generators

    vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
    xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
    def client_message(self, content):
        print("\nclient:", content)

        '''
        if len(content) > 1 or not UserEvent.isViewable(content[0]):
            # need to reset userEvent for unknown keys otherwise PttTerm.pre_refresh() would go wrong
            self.client_event(UserEvent.Unknown)
        '''

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
            resp = None
            b = content[n]

            if state != sNUM: number = ""

            c = chr(b)
            if state == None:
                cmdBegin = n
                if c == sESC:
                    state = sESC
                elif c == '\b':
                    resp = self.client_event(UserEvent.Key_Backspace)
                elif c == '\r':
                    resp = self.client_event(UserEvent.Key_Enter)
                elif b == IAC:
                    state = IAC
                elif UserEvent.isViewable(b):
                    resp = self.client_event(b)
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    if c == 'A':
                        resp = self.client_event(UserEvent.Key_Up, uncommitted)
                    elif c == 'B':
                        resp = self.client_event(UserEvent.Key_Down, uncommitted)
                    elif c == 'C':
                        resp = self.client_event(UserEvent.Key_Right)
                    elif c == 'D':
                        resp = self.client_event(UserEvent.Key_Left)
                    elif c == 'F':
                        resp = self.client_event(UserEvent.Key_End)
                    elif c == 'H':
                        resp = self.client_event(UserEvent.Key_Home)
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
                        resp = self.client_event(UserEvent.Key_PgUp)
                    elif number == 6:
                        resp = self.client_event(UserEvent.Key_PgDn)
                    elif number in [1, 7]:
                        resp = self.client_event(UserEvent.Key_Home)
                    elif number in [4, 8]:
                        resp = self.client_event(UserEvent.Key_End)
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

            if resp:    # a generator
                for ev in resp:
                    pass
            '''
            if isinstance(resp, bytes):
                # replace the current input with resp
                if not replaced: replace = content[:cmdBegin]
                replace += resp
                replaced = True
            elif resp is False:
                # drop current input
                if not replaced: replace = content[:cmdBegin]
                replaced = True
            elif replaced and state == None:
                replace += content[cmdBegin:n+1]
            '''

            n += 1

        if replaced: yield ProxyEvent(ProxyEvent.REPLACE, replace)

    # the client message will be dropped if false is returned
    # the current user event will be replaced if a bytes object is returned
    def client_event(self, event: UserEvent, uncommitted = False):
        print("User event:", UserEvent.name(event))

        if uncommitted:
            if event == UserEvent.Key_Up:
                self.cursor_up()
            elif event == UserEvent.Key_Down:
                self.cursor_down()

        self.userEvent = event

        if self.board:
            for ev in self.board.client_event(event):
                if ev._type == ProxyEvent.SWITCH:
                    print("terminal.client_event:", ev)
                    board = self.board
                    self.board = None
                    yield ProxyEvent(ProxyEvent.OUT_BOARD, board.name)

    def find_board(self):
        lines = self.screen.display
        if re.match("\s*文章選讀", lines[-1]):
            try:
                # In '系列' only displays the first thread for a series
                board = re.match("\s*【*(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》\s*$", lines[0]).group(3)
                print("In board: '%s'" % board)
            except (AttributeError, IndexError):
                print("Board missing: '%s'" % lines[0])
            else:
                if board not in self.boards:
                    self.boards[board] = PttBoard(board)
                return self.boards[board]
        return None

    def pre_server_message(self):
        if self.board:
            for event in self.board.pre_update():
                pass
        if False: yield

    def post_server_message(self):
        board = self.find_board()
        if board is not self.board:
            if self.board is None:
                event = ProxyEvent(ProxyEvent.IN_BOARD, board.name)
            else:
                event = ProxyEvent(ProxyEvent.OUT_BOARD, self.board.name)
                if board:
                    yield event
                    event = ProxyEvent(ProxyEvent.IN_BOARD, board.name)

            self.board = board
            print("post_server_message:", event)
            yield event
        elif self.board:
            for event in self.board.post_update():
                pass

    def server_message(self, content):
        print("server: (%d)" % len(content))
        self.feed(content)
        yield from self.post_server_message()

if __name__ == "__main__":
    PttTerminal(128, 32)

