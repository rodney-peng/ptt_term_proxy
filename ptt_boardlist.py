import sys
import os
import re

from ptt_menu import PttMenu, SearchBoard, QuickSwitch, HelpScreen, SearchBox, JumpToEntry
from ptt_event import ProxyEvent, ClientEvent
from ptt_board import PttBoard


class PttBoardList(PttMenu):

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
            if "請按任意鍵繼續" in lines[-1] or "動畫播放中" in lines[-1]:
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


