import re
import time
import traceback

from sortedcontainers import SortedDict

from ptt_event import ProxyEvent, ClientEvent, ClientContext
from ptt_menu import PttMenu, SearchBoard, SearchBox, QuickSwitch, HelpScreen, ThreadInfo, JumpToEntry, WhereAmI
from ptt_thread import PttThread
from ptt_command import CommandBox


class BoardCommandBox(CommandBox):

    Commands = { 'f': (lambda command: ProxyEvent.goto_viewed('first')),
                 'l': (lambda command: ProxyEvent.goto_viewed('last')),
                 'p': (lambda command: ProxyEvent.goto_viewed('prev')),
                 'n': (lambda command: ProxyEvent.goto_viewed('next')),
               }

    Patterns = { "viewed\s+(-?[0-9]+|\w+)": (lambda matched: ProxyEvent.goto_viewed(matched.group(1))),
                 "tagged\s+(-?[0-9]+|\w+)": (lambda matched: ProxyEvent.goto_tagged(matched.group(1))),
                 "banned\s+(-?[0-9]+|\w+)": (lambda matched: ProxyEvent.goto_banned(matched.group(1))),
               }


class BoardInfo(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("看板設定" in lines[-21] and "請按任意鍵繼續" in lines[-1])


class OnboardingScreen(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1])


class ReadUnreadConfig(PttMenu):

    @staticmethod
    def is_entered(lines):
        bg = yield ProxyEvent.req_cursor_background
        assert bg is not None
        yield ProxyEvent.ok

        yield ProxyEvent.as_bool(bg == "white" and \
                  lines[-4].startswith("設定已讀未讀記錄") and lines[-2].startswith("設定所有文章"))


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

class SortedThreads(SortedDict):

    def viewed(self, index, cursor = None):
        if not self: return None
        keys = self.keys()
        if index == 'first':
            nindex = 0
        elif index == 'last':
            nindex = -1
        elif index in ['prev', 'next']:
            if cursor is None: return None
            if cursor not in keys:
                if cursor < keys[0] or cursor > keys[-1]:
                    nindex = -1 if index == 'prev' else 0
                else:
                    i = 0
                    while cursor > keys[i]: i += 1
                    nindex = i-1 if index == 'prev' else i
            else:
                if index == 'prev':
                    nindex = self.index(cursor) - 1
                else:
                    nindex = (self.index(cursor) + 1) % len(self)
        else:
            try:
                nindex = int(index)
            except Exception:
                traceback.print_exc()
                return None
        try:
            key = keys[nindex]
        except Exception:
            traceback.print_exc()
            return None
        if key[:7].strip().isdecimal():
            return key[:7].strip().encode() + b'\r'
        else:
            return b'\x1b[4~'   # key <End>

    def tagged(self, index, cursor = None):
        return None

    def banned(self, index, cursor = None):
        return None

class PttBoard(PttMenu):

    def __init__(self, name):
        self.name = name
        super().__init__()

    def reset(self):
        super().reset()
        self._prefix = self.name
        self.onboarding = False
        self.firstKey = None
        self.threads = SortedThreads()
        self.commandEvents = []

        # TODO: use datetime.timedelta
        self.firstVisited = self.lastVisited = 0  # Epoch time
        self.elapsedTime = 0  # in seconds
        self.revisit = 0  # in number of revisit

    @staticmethod
    def is_entered(lines, board=None):
        # In '系列' only displays the first thread for a series
        title = re.match("【(板主:|徵求中).+(看板|系列|文摘)《([\w-]+)》$", lines[0].strip())
        if title:   # and (ignoreBottom or re.match("文章選讀", lines[-1].lstrip())):
            yield ProxyEvent(ProxyEvent.BOARD_NAME, title.group(3))
            yield ProxyEvent.as_bool((board == title.group(3)) if board else True)
        else:
            yield ProxyEvent.as_bool(False)

    def is_entered_self(self, lines):
        return ProxyEvent.eval_bool(self.is_entered(lines, self.name))

    def enter(self, y, x, lines):
        self.enteredTime = time.time()
        if not self.firstVisited:
            self.firstVisited = self.enteredTime
        else:
            self.revisit += 1

        yield from super().enter(y, x, lines)
        self.cursorLine = ""
        self.firstKey = None

        if "動畫播放中" in lines[-1] or "請按任意鍵繼續" in lines[-1]:
            self.onboarding = True
        elif lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), lines[y])

    def exit(self):
        self.lastVisited = time.time()
        elapsed = self.lastVisited - self.enteredTime
        if elapsed > 0: self.elapsedTime += elapsed
        yield from super().exit()

    def pre_client_event(self, y, x, content: bytes):
        if b'\x1b[A' in content or b'\x1b[B' in content:
            yield ProxyEvent.push_cursor

        yield from super().pre_client_event(y, x, content)

    def client_event(self, event: ClientEvent):
        class Commands:
            Names = ["GOTO_VIEWED", "GOTO_TAGGED", "GOTO_BANNED"]
            Commands = {getattr(ProxyEvent, name): getattr(self, name.lower()) for name in Names}

            @classmethod
            def __call__(cls, event):
                # e.g. ProxyEvent.GOTO_VIEWED: return self.goto_viewed(cls.lets_do_it, event.content)
                return cls.Commands[event._type](cls.lets_do_it, event.content)

            @classmethod
            def watched(cls, lets_do_it):
                cls.lets_do_it = lets_do_it
                return {getattr(ProxyEvent, name): cls() for name in cls.Names}

        if isinstance(self.subMenu, BoardCommandBox):
            lets_do_it = super().client_event(event)
            lets_do_exit = self.lets_do_subMenuExited(0, 0, [' '])
            yield from self.lets_do_if_return(lets_do_it, lets_do_exit, Commands.watched(lets_do_it))
            for event in self.commandEvents:
                yield event
            self.commandEvents = []
        else:
            yield from super().client_event(event)
            if self.subMenu: return

            if self.clientEvent == ClientEvent.x:
                yield ProxyEvent.drop_content
                yield from self.lets_do_new_subMenu(BoardCommandBox, 0, 0, [' '])

    def pre_update(self, y, x, lines, **kwargs):
        self.cursorMove = False
        if not self.onboarding:
            yield from super().pre_update(y, x, lines, **kwargs)
        yield ProxyEvent.purge_cursor(self.cursorMove)

    def post_update(self, y, x, lines):
        if self.onboarding:
            self.onboarding = not ProxyEvent.eval_bool(self.is_entered(lines))
        if not self.onboarding:
            yield from super().post_update(y, x, lines)

    def pre_update_pre_submenu(self, y, x, lines):
        if hasattr(self, "resumingSubMenu"): return
        yield from self.clear_marks(lines)

    @staticmethod
    def thread_key(line):
        if '★' in line[:6]:
            prefix = "?????? "  # ord('?') > ord('0'~'9')
            line = line[7:]
        else:
            prefix = line[1:8]
            line = line[8:]

        if '爆' in line[:2]:
            line = line[2:]
        else:
            line = line[3:]

        return prefix + line.rstrip()

    def makeThread(self, line, cached, thread = None):
        key = self.thread_key(line)
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

    re_cursorToBegin = b'\x1b\[[1-9]\d*;1H'
    re_cursorUpDown  = b'(\n+|' + re_cursorToBegin + b')'

    def pre_update_self(self, y, x, lines, **kwargs):
        self.cursorLine = lines[y]
        if hasattr(self, "resumingSubMenu"): return

        if 'peekData' in kwargs:
            peekData = kwargs['peekData']
            cursorUp = re.match(self.re_cursorToBegin + b'>.*\r' + self.re_cursorUpDown + b'.* .*' + self.re_cursorToBegin, peekData) is not None
            cursorDown = re.match(b' .*\r' + self.re_cursorUpDown + b'.*>.*\r', peekData) is not None
#            print(self.prefix(), "pre_update_self", cursorUp, cursorDown, peekData)
            self.cursorMove = cursorUp or cursorDown
            if not self.cursorMove:
                yield from self.clear_marks(lines)

        yield from super().pre_update_self(y, x, lines, **kwargs)

    subMenus = { ClientEvent.Ctrl_Z: QuickSwitch,
                 ClientEvent.s: SearchBoard,
                 ClientEvent.h: HelpScreen,
                 ClientEvent.Q: ThreadInfo,
                 ClientEvent.i: BoardInfo,
                 ClientEvent.I: BoardInfo,
                 ClientEvent.b: OnboardingScreen,
                 ClientEvent.v: ReadUnreadConfig,
                 ClientEvent.PoundSign:    SearchBox,
                 ClientEvent.Slash:        SearchBox,
                 ClientEvent.QuestionMark: SearchBox,
                 ClientEvent.a: SearchBox,
                 ClientEvent.Z: SearchBox,
                 ClientEvent.G: SearchBox,
                 ClientEvent.A: SearchBox,
                 ClientEvent.Ctrl_W: WhereAmI,
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
        # when returned from SearchBox searching for AIDC, the bottom line may be absent
        #self.returnedFromSearchBox = isinstance(self.subMenu, (SearchBox, JumpToEntry))

        resume_event = self.subMenu.to_be_resumed()
        if resume_event: self.resumingSubMenu = self.subMenu
        yield from super().lets_do_subMenuExited(y, x, lines)
        if resume_event: yield resume_event

        self.firstKey = None

    def post_update_is_self(self, y, x, lines):
        if not self.is_entered_self(lines):
            if ProxyEvent.eval_bool(PttThread.is_entered(lines)):
                # switch to another thread
                # cannot call makeThread() here since we don't have the title line
                self.subMenu = PttThread(self)
                yield from self.subMenuEntered()
                yield from self.subMenu.switch(y, x, lines)
            else:
                yield from self.exit()

    def post_update_self(self, y, x, lines, entered = False):
        if lines[y].startswith('>'):
            self.cursorLine = lines[y]
            print(self.prefix(), "post_update_self", lines[y])

        if hasattr(self, "resumingSubMenu"): return
        yield from self.mark_threads(lines)

    def goto_viewed(self, lets_do_it, index: str):
        print(self.prefix(), "viewed", index)
        data = self.threads.viewed(index.lower(), self.thread_key(self.cursorLine))
        if data is not None:
            event = ProxyEvent.send_to_server(data)
            self.commandEvents.append(event)

    def goto_tagged(self, lets_do_it, index: str):
        print(self.prefix(), "tagged", index)

    def goto_banned(self, lets_do_it, index: str):
        print(self.prefix(), "banned", index)

    FirstRow = 4
    MinColumns = 108
    MaxWidth = 8

    def mark_threads(self, lines):
        firstKey = self.thread_key(lines[self.FirstRow-1])
        if self.firstKey and self.firstKey == firstKey: return
        self.firstKey = firstKey

        columns = yield ProxyEvent.req_screen_column
        assert columns is not None
        yield ProxyEvent.ok
        if columns < self.MinColumns: return

        n = 0
        row = self.FirstRow
        col = self.MinColumns - self.MaxWidth
        for line in lines[self.FirstRow-1 : -1]:
            mark = ''
            key = self.thread_key(line)
            if key in self.threads:
                thread = self.threads[key]
                add_mark = (lambda _str, _mark: (_str + '/').lstrip('/') + _mark)
                if thread.tag_count(): mark = add_mark(mark, 'T')
                if thread.banned_count(): mark = add_mark(mark, 'B')
                if not mark: mark = 'V'
                content = "{:{width}}".format(mark, width = self.MaxWidth)
                yield ProxyEvent.draw_client(ClientContext(row, col, content, fg="brown", bold=True))
                n += 1
            row += 1
        if n:
            yield ProxyEvent.reset_rendition
            yield ProxyEvent.draw_cursor

    def clear_marks(self, lines):
        columns = yield ProxyEvent.req_screen_column
        assert columns is not None
        yield ProxyEvent.ok
        if columns < self.MinColumns: return

        n = 0
        row = self.FirstRow
        col = self.MinColumns - self.MaxWidth
        for line in lines[self.FirstRow-1 : -1]:
            key = self.thread_key(line)
            if key in self.threads:
                yield ProxyEvent.draw_client(ClientContext(row, col, ' ' * self.MaxWidth, fg="default"))
                n += 1
            row += 1
        if n:
            self.firstKey = None
            yield ProxyEvent.reset_rendition
            # cannot return the cursor here since it will be wrong position if the client message has Up or Down
            # use push_cursor and purge_cursor as workaround
            #yield ProxyEvent.draw_cursor

if __name__ == "__main__":
    from ptt_menu import test
    test(PttBoard("Test"))

