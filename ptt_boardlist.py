import sys
import os
import re

from ptt_menu import PttMenu, SearchBoard, QuickSwitch, HelpScreen, SearchBox, JumpToEntry, WhereAmI
from ptt_event import ProxyEvent, ClientEvent
from ptt_board import PttBoard


class PttBoardList(PttMenu):

    def reset(self):
        super().reset()
        self.boards = {}

    def is_entered(self, lines):
        entered = lines[0].lstrip().startswith("【看板列表】") and lines[-1].lstrip().startswith("選擇看板")
        if not entered and self.subMenu is None:
            board = ProxyEvent.eval_type(PttBoard.is_entered(lines), ProxyEvent.BOARD_NAME)
            if board:
                if board in self.boards:
                    self.subMenu = self.boards[board]
                else:
                    self.subMenu = PttBoard(board)

                    cached = yield ProxyEvent.req_submenu_cached
                    assert cached is not None
                    yield ProxyEvent.ok
                    if cached: self.boards[board] = self.subMenu

                entered = True

        yield ProxyEvent.as_bool(entered)

    def enter(self, y, x, lines):
        if self.subMenu:
            yield from super().enter(0, 0, [' '])
            yield from self.lets_do_new_subMenu(PttBoard, y, x, lines)
            return

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
                 ClientEvent.Slash:  SearchBox,
                 ClientEvent.Ctrl_W: WhereAmI,
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
                if board in self.boards:
                    self.subMenu = self.boards[board]
                else:
                    cached = yield ProxyEvent.req_submenu_cached
                    assert cached is not None
                    yield ProxyEvent.ok

                    self.subMenu = PttBoard(board)
                    if cached: self.boards[board] = self.subMenu
            yield ProxyEvent.as_bool(board is not None)
        else:
            yield from super().isSubMenuEntered(menu, lines)

    def post_update_is_self(self, y, x, lines):
        lets_do_it = self.is_entered(lines)
        def lets_do_yes():
            if self.subMenu: yield from self.lets_do_new_subMenu(PttBoard, y, x, lines)
        lets_do_no = self.exit()
        yield from self.lets_do_if(lets_do_it, lets_do_yes(), lets_do_no)

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), "post_update_self", lines[y])
        if False: yield

    def statistics(self):
        from datetime import timedelta

        boards  = len(self.boards)
        threads = sum([len(board.threads) for board in self.boards.values()])
        elapsed = sum([board.elapsedTime for board in self.boards.values()])

        delta = timedelta(seconds=round(elapsed))
        return f'{boards} boards, {threads} threads, elapsed {str(delta)}'


if __name__ == "__main__":
    print(PttBoardList().statistics())

