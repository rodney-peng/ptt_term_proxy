import re
import time

from ptt_event import ProxyEvent, ClientEvent
from ptt_menu import PttMenu, SearchBoard, SearchBox, QuickSwitch, HelpScreen, ThreadInfo, JumpToEntry
from ptt_thread import PttThread


class BoardInfo(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("看板設定" in lines[-21] and "請按任意鍵繼續" in lines[-1])


class OnboardingScreen(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1])


class PostThread(PttMenu):

    class PostHelp(PttMenu):

        @staticmethod
        def is_entered(lines):
            yield ProxyEvent.as_bool(re.match("瀏覽\s.+離開$", lines[-1].strip()) is not None)

        subMenus = { ClientEvent.h: HelpScreen }

    class PostTemplate(PttMenu):

        @staticmethod
        def is_entered(lines):
            yield ProxyEvent.as_bool(lines[-1].lstrip().startswith("【功能鍵】"))

        subMenus = { ClientEvent.h: HelpScreen }

    @staticmethod
    def is_entered(lines):
        def is_new_thread_screen(lines):
            for line in range(len(lines)-4, -1, -1):
                if lines[line].lstrip().startswith("發表文章") and lines[line+2].lstrip().startswith("種類：1.問題"):
                    return True
            return False

        bg = yield ProxyEvent.req_cursor_background
        assert bg is not None
        yield ProxyEvent.ok

        yield ProxyEvent.as_bool((bg == "white" and (is_new_thread_screen(lines) or lines[2].startswith("確定要儲存檔案嗎？"))) or \
                  lines[-1].lstrip().startswith("編輯文章") or \
                  lines[-1].lstrip().startswith("◆ 結束但不儲存 [y/N]?") )

    subMenus = { ClientEvent.Ctrl_Z: PostHelp,
                 ClientEvent.Ctrl_G: PostTemplate }


class PttBoard(PttMenu):

    def __init__(self, name):
        self.name = name
        super().__init__()

    def reset(self):
        super().reset()
        self._prefix = self.name
        self.onboarding = False
        self.threads = {}

        self.firstViewed = self.lastViewed = 0  # Epoch time
        self.elapsedTime = 0  # in seconds

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
        self.enteredTime = time.time()
        if not self.firstViewed: self.firstViewed = self.enteredTime

        yield from super().enter(y, x, lines)
        self.cursorLine = ""

        if "動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1]:
            self.onboarding = True
        elif lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), lines[y])

    def exit(self):
        self.lastViewed = time.time()
        elapsed = int(self.lastViewed - self.enteredTime)
        if elapsed > 0: self.elapsedTime += elapsed
        yield from super().exit()

    def pre_update(self, y, x, lines):
        if not self.onboarding:
            yield from super().pre_update(y, x, lines)

    def post_update(self, y, x, lines):
        if self.onboarding:
            self.onboarding = not ProxyEvent.eval_bool(self.is_entered(lines))
        if not self.onboarding:
            yield from super().post_update(y, x, lines)

    def makeThread(self, line, cached, thread = None):
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
        print(f"makeThread = '{key}' {thread} {key in self.threads}")
        if key in self.threads:
            if thread: self.threads[key].transfer(thread)
            thread = self.threads[key]
        else:
            if thread:
                thread.setKey(key)
            else:
                thread = PttThread(self, key)
            if cached: self.threads[key] = thread
        return thread

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
                 ClientEvent.PoundSign:    SearchBox,
                 ClientEvent.Slash:        SearchBox,
                 ClientEvent.QuestionMark: SearchBox,
                 ClientEvent.a: SearchBox,
                 ClientEvent.Z: SearchBox,
                 ClientEvent.G: SearchBox,
                 ClientEvent.A: SearchBox,
                 ClientEvent.Ctrl_P: PostThread,
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
                 ClientEvent.Key9: JumpToEntry,
               }

    def isSubMenuEntered(self, menu, lines):
        if menu is ThreadInfo:
            url = ProxyEvent.eval_type(menu.is_entered(lines), ProxyEvent.THREAD_URL)
            if url:
                cached = yield ProxyEvent.req_submenu_cached
                assert cached is not None
                yield ProxyEvent.ok

                self.makeThread(self.cursorLine, cached).setURL(url)
            print(self.prefix(), "URL:", url)
            yield ProxyEvent.as_bool(url is not None)
        else:
            yield from super().isSubMenuEntered(menu, lines)

    def makeSubMenu(self, menu):
        if menu is PttThread:
            cached = yield ProxyEvent.req_submenu_cached
            assert cached is not None
            yield ProxyEvent.ok

        if hasattr(self, "resumingSubMenu") and isinstance(self.resumingSubMenu, menu):
            self.subMenu = self.resumingSubMenu
            del self.resumingSubMenu
            if menu is PttThread:
                self.subMenu = self.makeThread(self.cursorLine, cached, self.subMenu)
        elif menu is PttThread:
            self.subMenu = self.makeThread(self.cursorLine, cached)
        else:
            yield from super().makeSubMenu(menu)

    def lets_do_subMenuExited(self, y, x, lines):
        resume_event = self.subMenu.to_be_resumed()
        if resume_event: self.resumingSubMenu = self.subMenu
        yield from super().lets_do_subMenuExited(y, x, lines)
        if resume_event: yield resume_event

    def post_update_is_self(self, y, x, lines):
        if not ProxyEvent.eval_bool(self.is_entered(lines, self.name)):
            if ProxyEvent.eval_bool(PttThread.is_entered(lines)):
                # switch to another thread
                # cannot call makeThread() here since we don't have the title line
                self.subMenu = PttThread(self)
                yield from self.subMenuEntered()
                yield from self.subMenu.switch(y, x, lines)
            else:
                yield from self.exit()

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), "post_update_self", lines[y])
        if False: yield


if __name__ == "__main__":
    from ptt_menu import test
    test(PttBoard("Test"))

