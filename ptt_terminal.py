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

from ptt_event import ClientEvent, ProxyEvent
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

    def __init__(self, columns, lines):
        self.screen = MyScreen(columns, lines)
        # self.stream = MyDebugStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)

        self.reset()

    def reset(self):
        self.clientEvent = ClientEvent.Unknown
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

    def cursor(self, strip=True):
        line = self.screen.display[self.screen.cursor.y]
        return (self.screen.cursor.y + 1, self.screen.cursor.x + 1,
                # rstrip() only, preserves leading spaces (and cursor)
                line.rstrip() if strip else line)

    def showCursor(self, lineAtCursor=True):
        y, x, line = self.cursor(False)
        print("Cursor:", y, x, end = " ")
        if lineAtCursor:
            print("'%s'" % line)
        else:
            print("lines: %d" % self.screen.lines)

    # messages from proxy and event generators

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
            resp = None
            b = content[n]

            if state != sNUM: number = ""

            c = chr(b)
            if state == None:
                cmdBegin = n
                if c == sESC:
                    state = sESC
                elif c == '\b':
                    resp = self.client_event(ClientEvent.Key_Backspace)
                elif c == '\r':
                    resp = self.client_event(ClientEvent.Key_Enter)
                elif b == IAC:
                    state = IAC
                elif ClientEvent.isViewable(b):
                    resp = self.client_event(b)
            elif state == sESC:
                if c == sCSI:
                    state = sCSI
                else:
                    state = None
            elif state == sCSI:
                if 'A' <= c <= 'H':
                    if c == 'A':
                        resp = self.client_event(ClientEvent.Key_Up, uncommitted)
                    elif c == 'B':
                        resp = self.client_event(ClientEvent.Key_Down, uncommitted)
                    elif c == 'C':
                        resp = self.client_event(ClientEvent.Key_Right)
                    elif c == 'D':
                        resp = self.client_event(ClientEvent.Key_Left)
                    elif c == 'F':
                        resp = self.client_event(ClientEvent.Key_End)
                    elif c == 'H':
                        resp = self.client_event(ClientEvent.Key_Home)
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
                        resp = self.client_event(ClientEvent.Key_PgUp)
                    elif number == 6:
                        resp = self.client_event(ClientEvent.Key_PgDn)
                    elif number in [1, 7]:
                        resp = self.client_event(ClientEvent.Key_Home)
                    elif number in [4, 8]:
                        resp = self.client_event(ClientEvent.Key_End)
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
                yield from resp

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

        if replaced: yield ProxyEvent(ProxyEvent.REPLACE_CONTENT, replace)

    # the client message will be dropped if false is returned
    # the current user event will be replaced if a bytes object is returned
    def client_event(self, event: ClientEvent, uncommitted = False):
        if uncommitted:
            if event == ClientEvent.Key_Up:
                self.cursor_up()
            elif event == ClientEvent.Key_Down:
                self.cursor_down()

        if self.board:
            yield from self.board.client_event(event)
            return

        print("User event:", ClientEvent.name(event))
        self.clientEvent = event
        if False: yield

    def pre_server_message(self):
        if self.board:
            y, x, line = self.cursor()
            for event in self.board.pre_update(y, x, line):
                if event._type < ProxyEvent.TERMINAL_START:
                    yield event
                else:
                    print("terminal.pre_server:", event)

        if False: yield

    def post_server_message(self):
        y, x, line = self.cursor()
        lines = self.screen.display
        if self.board:
            for event in self.board.post_update(y, x, lines):
                if event._type in [ProxyEvent.OUT_BOARD, ProxyEvent.RETURN, ProxyEvent.SWITCH]:
                    print("terminal: left", self.board.name)
                    self.board = None
                elif event._type < ProxyEvent.TERMINAL_START:
                    yield event
                else:
                    print("terminal.post_server:", event)
            if self.board: return

        if PttBoard.is_entered(lines):
            board = PttBoard.boardName(lines)
            if board not in self.boards:
                self.boards[board] = PttBoard(board)
            self.board = self.boards[board]
            for event in self.board.enter():
                if event._type < ProxyEvent.TERMINAL_START:
                    yield event

    def server_message(self, content):
        print("server: (%d)" % len(content))
        self.feed(content)
        yield from self.post_server_message()

if __name__ == "__main__":
    PttTerminal(128, 32)

