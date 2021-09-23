import os
import re
import time
import traceback

from uao import register_uao
register_uao()

from ptt_event import ClientEvent, ProxyEvent, ClientContext
from ptt_menu import PttMenu, SearchBoard, HelpScreen, ThreadInfo


class ThreadOption(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool("設定選項" in lines[-9] and "請調整設定" in lines[-1])


class JumpToPosition(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool(
                 (lines[-2].startswith("跳至此行") or lines[-2].startswith("跳至此頁")) and \
                  lines[-1].strip() == '')


class SearchThread(PttMenu):

    @staticmethod
    def is_entered(lines):
        bg = yield ProxyEvent.req_cursor_background
        assert bg is not None
        yield ProxyEvent.ok
        yield ProxyEvent.as_bool(bg == "white" and \
                  (lines[-2].startswith("[搜尋]關鍵字:") or lines[-2].startswith("區分大小寫(Y/N/Q)?")))


class ProxyCommand(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.true

    CommandRow = -1
    CommandCol = 60
    CommandPrompt = "Command:"
    CommandMaxLen = CommandCol - len(CommandPrompt)

    def enter(self, y, x, lines):
        yield from super().enter(y, x, lines)

        yield ProxyEvent.cut_stream(0)

        self.input = ""
        self.screenData = yield ProxyEvent.req_screen_data(ClientContext(self.CommandRow, 1, length=self.CommandCol))
        yield ProxyEvent.ok

        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1, self.CommandPrompt, fg="white", bg="black"))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8, " " * self.CommandMaxLen, fg="white", bg="black", bold=True))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8))

    def exit(self):
        yield from super().exit()
        yield ProxyEvent.send_to_client(self.screenData)
        yield ProxyEvent.draw_cursor
        yield ProxyEvent.resume_stream

    def client_event(self, event: ClientEvent):
        yield from super().client_event(event)
#        yield ProxyEvent.drop_content
        if ClientEvent.isViewable(event):
            if len(self.input) < self.CommandMaxLen:
                self.input += chr(event)
                yield ProxyEvent.event_to_client(event)  # echo
        elif event == ClientEvent.Backspace:
            if self.input:
                self.input = self.input[:-1]
                yield ProxyEvent.send_to_client(b'\b \b')
        elif event == ClientEvent.Enter:
            matched = re.match("ban\s+([0-9]+)", self.input.strip())
            if matched:
                yield ProxyEvent.ban_floor(int(matched.group(1)))
            yield from self.exit()

# a PTT thread being viewed
class PttThread(PttMenu):

    def __init__(self, board, key=None):
        self.board = board
        self.key = key
        super().__init__()

    def reset(self):
        super().reset()

        if self.key:
            # if the key doesn't start with 6 numbers and spaces, use the title string excluding the type like "[公告] "
            threadid = re.match("([0-9\s]{6})", self.key)
            threadid = threadid.group(1) if threadid else self.key[27+5:][:10]
            self._prefix = f"{self.board.name}/{threadid}"

        self.lines = []
        self.lastLine = 0
        self.floors = []
        self.lastFloorLine = 0
        self.bannedFloors = []

        self.url = None
        self.urlLine = 0

        self.firstViewed = self.lastViewed = 0  # Epoch time
        self.elapsedTime = 0  # in seconds

        self.atBegin = self.atEnd = False
        self.viewedFirstLine = 0
        self.viewedLastLine = 0
        self.viewedLastRow = 0

    def setURL(self, url: str):
        if url != self.url:
            self.url = url
            self.urlLine = 0
            self.lastFloorLine = 0
            self.bannedFloors = []
            if self.viewedFirstLine and self.viewedLastLine:
                self.scanFloor(self.viewedFirstLine, self.viewedLastLine)

    @classmethod
    def is_entered(cls, lines):
        yield ProxyEvent.as_bool(re.match("\s*瀏覽", lines[-1]) and re.search("\(←\)離開\s*$", lines[-1]))

    @classmethod
    def setEnteringTrigger(cls, event):
        cls.enteringTrigger = event

    @classmethod
    def lets_do_enteringTrigger(cls):
        event = getattr(cls, "enteringTrigger", None)
        if event:
            print("PttThread.enteringTrigger", event)
            yield event
            del cls.enteringTrigger

    def enter(self, y, x, lines):
        yield from self.lets_do_enteringTrigger()

        yield from super().enter(y, x, lines)
        if self.url: print(self.prefix(), "has URL", self.url)

    def switch(self, y, x, lines):
        yield from super().switch(y, x, lines)

        # re-enter to let the parent have the correct key
        yield ProxyEvent.cut_stream(2)
        yield ProxyEvent.event_to_server(ClientEvent.q)   # to return to the board
        # to re-enter the thread once it exited
        # don't yield directly as 'q' and 'r' almost arriving at the same time may confuse the server
        self.resume_event = ProxyEvent.event_to_server(ClientEvent.r)
        self.setEnteringTrigger(ProxyEvent.resume_stream)

    def client_event(self, event: ClientEvent):
        if isinstance(self.subMenu, ProxyCommand):
            lets_do_it = super().client_event(event)
            lets_do_exit = self.lets_do_subMenuExited(0, 0, [])
            ban_floor = (lambda event: self.ban_floor(event.content))
            yield from self.lets_do_if_return(lets_do_it, lets_do_exit, {ProxyEvent.BAN_FLOOR: ban_floor})
        else:
            yield from super().client_event(event)
            if self.subMenu is None and self.clientEvent == ClientEvent.x:
                yield ProxyEvent.drop_content
                yield from self.lets_do_new_subMenu(ProxyCommand, 0, 0, [])

    def pre_update_pre_submenu(self, y, x, lines):
        if self.viewedLastRow:
            yield from self.lets_do_clearFloor()

    def pre_update_is_self(self, y, x, lines):
        if self.viewedLastRow:
            yield from self.lets_do_clearFloor()

        # not include 't' which is determined in post_update_is_self() because
        # 't' goes to the next page or jumps to the next article in the same series,
        # and can only be determined in post_update_is_self().
        if self.is_switch_event(self.clientEvent):
            yield from self.exit()

    # switch to another thread
    def is_switch_event(self, event: ClientEvent):
        return (event == ClientEvent.Key_Up and self.atBegin) or \
               (event in [ClientEvent.Key_Down, ClientEvent.Key_Right, ClientEvent.Enter] and self.atEnd) or \
               (chr(event) in "fb[]+-=Aa")

    subMenus = { ClientEvent.s: SearchBoard,
                 ClientEvent.h: HelpScreen,
                 ClientEvent.o: ThreadOption,
                 ClientEvent.Q: ThreadInfo,
                 ClientEvent.Slash: SearchThread,
                 ClientEvent.Key1: JumpToPosition,
                 ClientEvent.Key2: JumpToPosition,
                 ClientEvent.Key3: JumpToPosition,
                 ClientEvent.Key4: JumpToPosition,
                 ClientEvent.Key5: JumpToPosition,
                 ClientEvent.Key6: JumpToPosition,
                 ClientEvent.Key7: JumpToPosition,
                 ClientEvent.Key8: JumpToPosition,
                 ClientEvent.Key9: JumpToPosition,
                 ClientEvent.Colon:     JumpToPosition,
                 ClientEvent.SemiColon: JumpToPosition,
                 #ClientEvent.x: ProxyCommand,  # not work here since server won't send any data after 'x'
               }

    def isSubMenuEntered(self, menu, lines):
        # once in ThreadInfo, it exits to the board rather than the thread.
        # we will get back to where we are in lets_do_subMenuExited().
        if menu is ThreadInfo:
            url = None
            lets_do_it = menu.is_entered(lines)
            def catch_url(event):
                nonlocal url
                url = event.content
            yield from self.evaluate(lets_do_it, {ProxyEvent.THREAD_URL: catch_url})
            if url: self.setURL(url)
            yield ProxyEvent.as_bool(url is not None)
        else:
            yield from super().isSubMenuEntered(menu, lines)

    def lets_do_subMenuExited(self, y, x, lines):
        if isinstance(self.subMenu, ThreadInfo):
            yield ProxyEvent.send_to_server(b'\r')   # back to the thread
            if self.viewedFirstLine > 1:
                # back to the position
                #yield ProxyEvent.send_to_server(b':%d\r' % self.viewedFirstLine)
                self.setEnteringTrigger(ProxyEvent.send_to_server(b':%d\r' % self.viewedFirstLine))
        yield from super().lets_do_subMenuExited(y, x, lines)

    def post_update_is_self(self, y, x, lines):
        if self.clientEvent == ClientEvent.t:
            browse = re.match("\s*瀏覽.+\(\ *?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", lines[-1])
            if browse is None or int(browse.group(2)) == 1:
                yield from self.exit()
        else:
            yield from super().post_update_is_self(y, x, lines)

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        yield from self.lets_do_view(lines)

    def ban_floor(self, floor: int):
        if floor > 0 and floor in self.floors and floor not in self.bannedFloors:
            print(self.prefix(), "banned", floor)
            self.bannedFloors.append(floor)
            line = self.floors.index(floor) + 1
            yield from self.ban_line(line, False)

    def unban_floor(self, floor: int):
        if floor > 0 and floor in self.bannedFloors:
            print(self.prefix(), "unbanned", floor)
            self.bannedFloors = [f for f in self.bannedFloors if f != floor ]

    def ban_line(self, line: int, cursor_return: bool):
        def line_width(text):
            return sum([(2 if ord(c) > 0xff else 1) for c in text])

        if self.viewedFirstLine <= line <= self.viewedLastLine:
            row = self.viewedLastRow - (self.viewedLastLine - line)
            prefix = re.match("(推|噓|→) [0-9A-Za-z]+\s*:\s", self.lines[line-1])
#            print(self.prefix(), "ban_line: prefix: '%s'" % prefix.group(0))
            suffix = re.search("(\s+([0-9.]+\s)?[0-9/]+\s[0-9:]+\s*)$", self.lines[line-1])
#            print(self.prefix(), "ban_line: suffix: '%s'" % suffix.group(0))
            banned = self.lines[line-1][len(prefix.group(0)) : len(suffix.group(0)) * -1]
#            print(self.prefix(), "ban_line: banned: '%s'" % banned)
            col = line_width(prefix.group(0)) + 1
            content = '*' * line_width(banned)
            yield ProxyEvent.draw_client(ClientContext(row, col, content))
            if cursor_return: yield ProxyEvent.draw_cursor

    LINE_HOLDER = chr(0x7f)

    def lets_do_view(self, lines):
        browse = re.match("\s*瀏覽.+\(\ *?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", lines[-1])
        if browse is None: return
        screenLines = lines
        lines = lines[0:-1]

        percent = int(browse.group(1))
        first = int(browse.group(2))
        last  = int(browse.group(3))

        assert 0 < first <= last
        assert last - first + 1 <= len(lines)

        self.atBegin = (first == 1)
        self.atEnd = (percent == 100)

        if self.firstViewed == 0: self.firstViewed = time.time()

        if self.lastLine < last:
            # which is better?
            #   [self.LINE_HOLDER for _ in range(last - self.lastLine)]
            #   self.LINE_HOLDER * (last - self.lastLine)
            self.lines.extend(self.LINE_HOLDER * (last - self.lastLine))
            self.floors.extend([0] * (last - self.lastLine))
            self.lastLine = last

#        print("View lines:", first, last, "curr:", len(self.lines), self.lastLine)

        i = 0
        f = first
        text = ""
        while i < len(lines) and f <= last:
            line = lines[i].rstrip()
            # it's assummed the minimum screen width is 80 and line-wrap occurrs only after 78 characters
            if len(line.encode("big5uao", "replace")) > 78 and line[-1] == '\\':
                text += line[0:-1]
            else:
                self.lines[f-1] = text + line
#                print("add [%d]" % f, "'%s'" % self.lines[f-1])
                text = ""
                f += 1
            i += 1

        if text and f <= last:
            self.lines[f-1] = text
#            print("add [%d]" % f, "'%s'" % self.lines[f-1])
            f += 1

        if f <= last:
            yield ProxyEvent.warning(self.prefix() + " Caution: line wrap is probably missing!")

        self.viewedFirstLine = first
        self.viewedLastLine = last
        self.viewedLastRow = 0  # also means floor in view if not 0, to be confirmed by lets_do_showFloor()

        self.scanFloor(first, last)
        yield from self.lets_do_showFloor(first, last, i)
        yield from self.lets_do_banFloor(first, last, i)

    def lets_do_banFloor(self, firstLine, lastLine, lastRow):
        if self.viewedLastRow == 0: return
        banned = False
        firstLine = max(firstLine-1, self.urlLine)
        for line in range(lastLine, firstLine, -1):
            if not self.floors[line-1]: continue
            if self.floors[line-1] not in self.bannedFloors: continue
            banned = True
            yield from self.ban_line(line, False)
        if banned:
            yield ProxyEvent.draw_cursor

    minColumns = 86
    maxWidth = 5

    def lets_do_showFloor(self, firstLine, lastLine, lastRow):
        if not self.urlLine: return

        columns = yield ProxyEvent.req_screen_column
        assert columns is not None
        yield ProxyEvent.ok
        if columns < self.minColumns: return

        def floorStr(floor):
            if floor:
                return "{:^{width}}".format(floor, width = self.maxWidth)
            else:
                return ' ' * self.maxWidth

        inView = False
        firstLine = max(firstLine-1, self.urlLine)
        for i in range(lastLine, firstLine, -1):
            if not self.floors[i-1]: continue
            inView = True
            row = lastRow - (lastLine - i)
            col = self.minColumns + 1 - self.maxWidth
            floor = floorStr(self.floors[i-1])
#            print(self.prefix(), f"floor [{row:2}, {col:2}] = '{floor}'")
            yield ProxyEvent.draw_client(ClientContext(row, col, floor))
        if inView:
            yield ProxyEvent.draw_cursor
            self.viewedLastRow = lastRow    # confirms floor is in view

    def lets_do_clearFloor(self):
        if not self.viewedLastRow: return
        firstLine = max(self.viewedFirstLine-1, self.urlLine)
        lastLine = self.viewedLastLine
        for i in range(lastLine, firstLine, -1):
            if not self.floors[i-1]: continue
            row = self.viewedLastRow - (lastLine - i)
            col = self.minColumns + 1 - self.maxWidth
            floor = ' ' * self.maxWidth
#            print(self.prefix(), f"clear floor [{row:2}, {col:2}] = '{floor}'")
            yield ProxyEvent.draw_client(ClientContext(row, col, floor))
        self.viewedLastRow = 0

    def scanFloor(self, first: int, last: int):
        re_push_msg = "(推|噓|→) [0-9A-Za-z]+\s*:"
        if not self.scanURL(): return False
        if self.lastFloorLine:
            floor = self.floors[self.lastFloorLine - 1]
            line = self.lastFloorLine
        else:
            floor = 0
            line = self.urlLine
        while line < first-1:
            if self.lines[line] == self.LINE_HOLDER: return False
            if re.match(re_push_msg, self.lines[line]):
                floor += 1
                self.floors[line] = floor
                self.lastFloorLine = line + 1
#                print("line:", line+1, "floor:", floor)
            else:
                self.floors[line] = 0
            line += 1
        while line <= last-1:
            if self.lines[line] == self.LINE_HOLDER: return False
            if re.match(re_push_msg, self.lines[line]):
                floor += 1
                self.floors[line] = floor
                self.lastFloorLine = line + 1
#                print("(line):", line+1, "(floor):", floor)
            else:
                self.floors[line] = 0
            line += 1

    def scanURL(self):
        if self.lastLine < 3:
            return None
        if self.url and self.urlLine:
            return self.url

        if self.url:
            # top-down as we are confident what the URL is
            i = 2
            while i < self.lastLine - 2:
                if self.lines[i].startswith("※ 發信站: 批踢踢實業坊") and \
                   self.lines[i+1].startswith("※ 文章網址:") and \
                  (self.lines[i+1])[7:].strip() == self.url:
                    self.urlLine = (i+1)+1
                    # article lines has no floor
                    self.floors[0:self.urlLine] = [None] * self.urlLine
                    print("scanURL top-down:", self.url, "at", self.urlLine)
                    return self.url
                i += 1
        else:
            # bottom-up to try to avoid collision
            i = self.lastLine - 3
            while i > 0:
                # there is thread without the leading "--" line
                if self.lines[i] == "--" and \
                   self.lines[i+1].startswith("※ 發信站: 批踢踢實業坊") and \
                   self.lines[i+2].startswith("※ 文章網址:"):
                    self.url = (self.lines[i+2])[7:].strip()
                    self.urlLine = (i+2)+1
                    # article lines has no floor
                    self.floors[0:self.urlLine] = [None] * self.urlLine
                    print("scanURL bottom-up:", self.url, "at", self.urlLine)
                    return self.url
                i -= 1
        return None

    def text(self, first = 1, last = -1):
#        print("text:", first, last, self.lastLine)
        if first < 0: first = self.lastLine + 1 + first
        if last < 0: last = self.lastLine + 1 + last
#        print("text:", first, last, self.lastLine)

        text = ""
        while 0 < first <= last <= self.lastLine:
#            print("line [%d]" % first, "'%s'" % self.lines[first-1])
            text += ((self.lines[first-1] if self.lines[first-1] != self.LINE_HOLDER else '') + '\n')
            first += 1
        return text

    def show(self, complete=True):
        def sec2time(seconds):
            time_str = ""
            if seconds // 3600:
                time_str += "%d hr" % (seconds // 3600)
                seconds %= 3600
            if seconds // 60:
                if time_str: time_str += " "
                time_str += "%d min" % (seconds // 60)
                seconds %= 60
            if time_str: time_str += " "
            time_str += "%d sec" % seconds
            return time_str

        url = self.scanURL()
        print("\nThread lines:", self.lastLine, "url:", url)
        if self.firstViewed: print("firstViewed:", time.ctime(self.firstViewed))
        if self.lastViewed:  print("lastViewed:", time.ctime(self.lastViewed))
        print("Elapsed:", sec2time(self.elapsedTime))
        if url:
            board, fn = self.url2fn(url)
            aidc = self.fn2aidc(fn)
            print("board:", board, "fn:", fn, "aidc:", aidc)
        if complete:
            print(self.text())
        else:
            print(self.text(1, 3))
            print(self.text(-3))
        print()

    # Article IDentification System
    # https://github.com/ptt/pttbbs/blob/master/docs/aids.txt
    def aids(self):
        url = self.scanURL()
        if url is None: return None

        board_fn = self.url2fn(url)
        if board_fn is None: return None

        aidc = self.fn2aidc(board_fn[1])
        if aidc is None: return None

        return url, board_fn[0], board_fn[1], aidc

    # FN: filename
    # AIDu: uncompressed article number
    # AIDc: compressed article number
    @staticmethod
    def url2fn(url):
        result = re.match("https?://www.ptt.cc/bbs/(.+)/(.+)\.html", url)
        if not result: return None

        board = result.group(1)
        fn    = result.group(2)
        return board, fn

    ENCODE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
    @classmethod
    def fn2aidc(cls, fn):
        result = re.match("(.)\.(\d+)\.A\.([0-9A-F]{3})", fn)
        if not result: return None

        m = 0 if result.group(1) == 'M' else 1
        hi = int(result.group(2)) & 0xffffffff
        lo = int(result.group(3), 16) & 0xfff
        aidu = (((m << 32) | hi) << 12) | lo
        aidc = ''
        aidc += cls.ENCODE[(m << 2) | (hi >> 30)]
        aidc += cls.ENCODE[(hi >> 24) & 0x3f]
        aidc += cls.ENCODE[(hi >> 18) & 0x3f]
        aidc += cls.ENCODE[(hi >> 12) & 0x3f]
        aidc += cls.ENCODE[(hi >>  6) & 0x3f]
        aidc += cls.ENCODE[ hi        & 0x3f]
        aidc += cls.ENCODE[lo >> 6]
        aidc += cls.ENCODE[lo & 0x3f]
        return aidc

    # =================================

    def reload(self, retired):
        # works only if all attributes are system-defined objects
        vars(self).update(vars(retired))

    # remove attributes which don't need to persist
    # It's for PttThreadPersist only but is here for symmetrical purpose.
    # When attributes are changed in clear(), change in removeForPickling() and initiateUnpickled() as well.
    @staticmethod
    def removeForPickling(state):
        # only self.lines is initiated in PttThreadPersist.__setstate__()
        del state['lines']
        if 'floors'  in state: del state['floors']
        if 'atBegin' in state: del state['atBegin']
        if 'atEnd'   in state: del state['atEnd']
        if 'persistent'      in state: del state['persistent']
        if 'waitingForInput' in state: del state['waitingForInput']
        return state

    # initiate attributes removed by removeForPickling() but are needed by PttThreadPersist
    def initiateUnpickled(self):
        self.lines = []
        self.floors = []
        if not hasattr(self, "urlLine"): self.urlLine = 0
        if not hasattr(self, "lastFloorLine"): self.lastFloorLine = 0

    def loadContent(self, filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                self.lines = [line.rstrip("\n") for line in f.readlines()]
        except FileNotFoundError:
            return False
        else:
            self.lastLine = len(self.lines)
            print("Load from ", filename, "lines:", self.lastLine)
            return True

    def saveContent(self, filename):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                for line in self.lines:
                    f.write((line if line != self.LINE_HOLDER else '') + '\n')
                print("Write", filename, "bytes", f.tell())
        except Exception as e:
            traceback.print_exc()

    from ptt_event import UserEvent

    # deliberate to prohibit thread switch by Up/BS at the first line, or Down/Enter/Space at the last line
    # It makes little sense to me to browse thread blindly. Use those in isSwitchEvent() if desired.
    def is_prohibited(self, event: UserEvent):
        return False if self.waitingForInput else ( \
               (event in [UserEvent.Key_Up, UserEvent.Backspace] and self.atBegin) or \
               (event in [UserEvent.Key_Down, UserEvent.Enter, UserEvent.Space] and self.atEnd) )

    def _switch_(self, pickler):
        if self.lastLine == 0: return False
        assert self.firstViewed > 0

        self.lastViewed = time.time()
        elapsed = self.lastViewed - self.firstViewed
        if elapsed > 0: self.elapsedTime = elapsed

        if self.persistent: pickler(self)

        self.clear()
        return True

    def mergedLines(self, lines, newline=False):
        '''
        merge with existing content:
        if new content line is not empty(has LINE_HOLDER only), always overwrites existing one
            otherwise skip to the next line.
        '''
        def filter(line):
            if len(line):
                if newline and line[-1] != '\n':
                    line += '\n'
                elif not newline and line[-1] == '\n':
                    line = line.rstrip('\n')
            else:
                line = ('\n' if newline else self.LINE_HOLDER)
            return line

        existing = len(lines)
        for n, text in enumerate(self.lines):
            if text != self.LINE_HOLDER:
                yield filter(text)
            elif n < existing:
                yield filter(lines[n])
            else:
                yield filter('')

        if len(self.lines) == 0:
            n = 0
        else:
            n += 1
        print("new lines:", n)
        while n < existing:
            yield filter(lines[n])
            n += 1
        print("total lines:", n)


# A PttThread object will be sent to the persistence server through normal pickling,
# then the object is merged to a PttThreadPersist object for persistence.
# A PttThreadPersist object is the accumulated status of the same PttThread objects.
class PttThreadPersist(PttThread):

    # https://docs.python.org/3/library/pickle.html#handling-stateful-objects
    # called upon pickling (save to shelve)
    def __getstate__(self):
        state = self.__dict__.copy()
        return self.removeForPickling(state)

    # called upon construction or unpickling (create new instance or load from shelve)
    # Be cautious attributes removed from removeForPickling() don't exist
    def __setstate__(self, state):
        self.__dict__.update(state)
        self.initiateUnpickled()

    def view(self, lines, first: int, last: int, atEnd: bool):
        raise AssertionError("Viewing a persistent thread is invalid!")

    def text(self, first = 1, last = -1):
        return "<empty>" if len(self.lines) == 0 else super().text(first, last)

    def merge(self, thread):
        self.lines = [line for line in self.mergedLines(thread.lines)]
        self.lastLine = len(self.lines)
        self.url = thread.url

        if self.firstViewed == 0:
            self.firstViewed = thread.firstViewed
        self.lastViewed = thread.lastViewed
        self.elapsedTime += thread.elapsedTime


def test(thread):
    thread.url = "http://www.ptt.cc/bbs/Lifeismoney/M.1630497037.A.786.html"
    lines = [ "",
              "a" * 120,
              "",
              "b" * 240,
              ""
              "--",
              "※ 發信站: 批踢踢實業坊",
              "※ 文章網址: " + thread.url,
              "",
              "推 abc: dldldldf",
              "",
              "噓 123: 34343",
              "→ dkfjkkdf: kdkdkdk" ]

    i = 0
    last = 0
    screen = []
    while lines[i] != "--":
        line = []
        while len(lines[i]) > 80:
            line.append(lines[i][:80] + '\\')
            lines[i] = lines[i][80:]
        line.append(lines[i])
        screen.extend(line)
        last += 1
        i += 1

    screen.extend(lines[i:])
    last += (len(lines) - i)
    screen.append(f" 瀏覽 ( 50%) 目前顯示: 第 1~{last} 行")

    for i in range(len(screen)):
        print(f"{i+1:2} '{screen[i]}'")

    lets_do_it = thread.lets_do_view(screen)
    for event in lets_do_it:
        print("view:", event)
        if event._type == ProxyEvent.REQ_SCREEN_COLUMN:
            lets_do_it.send(129)

    thread.show()
    print("lastLine", thread.lastLine, "lastFloor", thread.lastFloorLine)
    print(thread.floors)

    # test thread switch
    for event in thread.client_event(ClientEvent.Key_Up):
        print("client_event:", event)
    for event in thread.pre_update(0, 0, screen):
        print("pre_update:", event)

    lets_do_it = thread.enter(0, 0, screen)
    for event in lets_do_it:
        print("enter:", event)
        if event._type == ProxyEvent.REQ_SCREEN_COLUMN:
            lets_do_it.send(129)

if __name__ == "__main__":
    from ptt_board import PttBoard
    from ptt_menu import test as testMenu
    thread = PttThread(PttBoard("Test"), "123456")
    testMenu(thread)
    test(thread)

