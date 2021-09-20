import re

from ptt_event import ProxyEvent, ClientEvent
from ptt_menu import PttMenu, SearchBoard, QuickSwitch, HelpScreen, ThreadInfo
from ptt_thread import PttThread


class JumpToEntry(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool(lines[-1].strip().startswith("跳至第幾項:"))


class BoardInfo(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("看板設定" in lines[-21] and "請按任意鍵繼續" in lines[-1])


class OnboardingScreen(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1])


class PttBoard(PttMenu):

    def __init__(self, name):
        self.name = name
        super().__init__()

    def reset(self):
        super().reset()
        self._prefix = self.name
        self.onboarding = False
        self.threads = {}

    @staticmethod
    def is_entered(lines, board=None):
        # In '系列' only displays the first thread for a series
        title = re.match("\s*【*(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》\s*$", lines[0])
        if title and re.match("\s*文章選讀", lines[-1]):
            yield ProxyEvent(ProxyEvent.BOARD_NAME, title.group(3))
            yield ProxyEvent.as_bool((board == title.group(3)) if board else True)
        else:
            yield ProxyEvent.as_bool(False)

    def enter(self, y, x, lines):
        yield from super().enter(y, x, lines)
        self.cursorLine = ""

        if "動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1]:
            self.onboarding = True
        elif lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), lines[y])

    def pre_update(self, y, x, lines):
        if not self.onboarding:
            yield from super().pre_update(y, x, lines)

    def post_update(self, y, x, lines):
        if self.onboarding:
            self.onboarding = not ProxyEvent.eval_bool(self.is_entered(lines))
        if not self.onboarding:
            yield from super().post_update(y, x, lines)

    def makeThread(self, line):
        if '★' in line[:6]:
            prefix = "******"
            line = line[6:]
        else:
            prefix = line[1:7]
            line = line[7:]

        if '爆' in line[:4]:
            line = line[3:]
        else:
            line = line[4:]

        key = prefix + line.rstrip()
        print(f"thread = '{key}'")
        if key not in self.threads:
            self.threads[key] = PttThread(self, key)
        return self.threads[key]

    def pre_update_self(self, y, x, lines):
        self.cursorLine = lines[y]
        yield from super().pre_update_self(y, x, lines)

    subMenus = { ClientEvent.Ctrl_Z: QuickSwitch,
                 ClientEvent.s: SearchBoard,
                 ClientEvent.h: HelpScreen,
                 ClientEvent.Q: ThreadInfo,
                 ClientEvent.i: BoardInfo,
                 ClientEvent.I: BoardInfo,
                 ClientEvent.b: OnboardingScreen,
                 ClientEvent.Enter:     PttThread,
                 ClientEvent.Key_Right: PttThread,
                 ClientEvent.l:         PttThread,
                 ClientEvent.r:         PttThread,
                 ClientEvent.Key0: JumpToEntry,
                 ClientEvent.Key1: JumpToEntry,
                 ClientEvent.Key2: JumpToEntry,
                 ClientEvent.Key3: JumpToEntry,
                 ClientEvent.Key4: JumpToEntry,
                 ClientEvent.Key5: JumpToEntry,
                 ClientEvent.Key6: JumpToEntry,
                 ClientEvent.Key7: JumpToEntry,
                 ClientEvent.Key8: JumpToEntry,
                 ClientEvent.Key9: JumpToEntry }

    def isSubMenuEntered(self, menu, lines):
        if menu is ThreadInfo:
            url = ProxyEvent.eval_type(menu.is_entered(lines), ProxyEvent.THREAD_URL)
            if url: self.makeSubMenu(PttThread).setURL(url)
            print(self.prefix(), "URL:", url)
            yield ProxyEvent.as_bool(url is not None)
        else:
            yield from super().isSubMenuEntered(menu, lines)

    def makeSubMenu(self, menu):
        if menu is PttThread:
            print(self.prefix(), "makeSubMenu", self.cursorLine)
            return self.makeThread(self.cursorLine)
        else:
            return super().makeSubMenu(menu)

    def lets_do_subMenuExited(self, y, x, lines):
        resume_event = self.subMenu.to_be_resumed()
        yield from super().lets_do_subMenuExited(y, x, lines)
        if resume_event: yield resume_event

    def post_update_is_self(self, y, x, lines):
        if not ProxyEvent.eval_bool(self.is_entered(lines, self.name)):
            if ProxyEvent.eval_bool(PttThread.is_entered(lines)):
                # switch to another thread
                self.subMenu = PttThread(self) # cannot call makeThread() here since we don't have the title line
                yield from self.subMenuEntered()
                yield from self.subMenu.switch(y, x, lines)
            else:
                yield from self.exit()

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), "post_update_self", lines[y])
        if False: yield

    def is_cursor_moved(self):
        print(self.prefix(), "is_cursor_moved", ClientEvent.name(self.clientEvent))
        return self.clientEvent in [ClientEvent.Key_Up, ClientEvent.Key_Down, ClientEvent.Key_PgUp, ClientEvent.Key_PgDn,
                                    ClientEvent.Key_Home, ClientEvent.Key_End, ClientEvent.Ctrl_B, ClientEvent.Ctrl_F] or \
               chr(self.clientEvent) in "pknjPN$=[]<>-+S{}"

if __name__ == "__main__":
    from ptt_menu import test
    test(PttBoard("Test"))

