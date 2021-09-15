import re

from ptt_event import ProxyEvent, ClientEvent
from ptt_menu import PttListMenu, SearchBoard, HelpScreen
from ptt_thread import PttThread

class PttBoard(PttListMenu):

    def __init__(self, name):
        self.name = name
        super().__init__()

    def reset(self):
        super().reset()
        self._prefix = self.name

        self.threads = {}
        self.subMenu = None

    @classmethod
    def is_entered(cls, lines, board=None):
        # In '系列' only displays the first thread for a series
        title = re.match("\s*【*(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》\s*$", lines[0])
        if title and re.match("\s*文章選讀", lines[-1]):
            return (board == title.group(3)) if board else True
        return False

    @classmethod
    def boardName(cls, lines):
        return re.match("\s*【*(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》\s*$", lines[0]).group(3)

    def thread(self, line):
        prefix = 7
        stats  = 5
        if '★' in line[:6]: prefix -= 1
        if '爆' in line[prefix:prefix+4]: stats -= 1
        thread_key = line[1:prefix] + line[prefix+stats:]
        print(f"thread = '{thread_key}'")
        if thread_key not in self.threads:
            self.threads[thread_key] = PttThread(self, thread_key)
        return self.threads[thread_key]

    def pre_update_submenu(self, y, x, line):
        if not self.subMenu: return
        yield from self.subMenu.pre_update(y, x, line)

    def pre_update_self(self, y, x, line):
        self.cursorLine = line
        if False: yield

    def post_update_submenu(self, y, x, lines):
        if not self.subMenu: return

        quitMenu = False
        for event in self.subMenu.post_update(y, x, lines):
            if event._type == ProxyEvent.RETURN:
                quitMenu = True
            elif event._type == ProxyEvent.SWITCH:
                quitMenu = True
                yield event
            else:
                yield event

        if quitMenu:
            self.subMenu = None
        else:
            yield ProxyEvent(ProxyEvent.DONE)

        if False: yield

    subMenus = { ClientEvent.s: SearchBoard, ClientEvent.h: HelpScreen,
                 ClientEvent.Key_Enter: PttThread, ClientEvent.Key_Right: PttThread, ClientEvent.r: PttThread }

    def post_update_self(self, y, x, lines):
        if self.clientEvent in self.subMenus:
            menu = self.subMenus[self.clientEvent]
            if menu.is_entered(lines):
                if menu is PttThread:
                    self.subMenu = self.thread(self.cursorLine)
                else:
                    self.subMenu = menu()
                yield from self.subMenu.enter()
                return
        elif self.clientEvent in [ClientEvent.q, ClientEvent.Key_Left] and \
             not self.is_entered(lines, self.name):
            yield ProxyEvent(ProxyEvent.OUT_BOARD, self.name)


if __name__ == "__main__":
    board = PttBoard("Test")
    for event in board.client_event(ClientEvent.Key_Space):
        print(event)

