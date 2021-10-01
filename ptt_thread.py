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
        yield ProxyEvent.as_bool(("設定選項" in lines[-9] or "快速設定" in lines[-3]) and "請調整設定" in lines[-1])


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


class UnableToReply(PttMenu):

    @staticmethod
    def is_entered(lines):
        bg = yield ProxyEvent.req_cursor_background
        assert bg is not None
        yield ProxyEvent.ok

        yield ProxyEvent.as_bool(bg == "white" and \
                  lines[-2].startswith("▲ 無法回應至看板。 改回應至 (M)作者信箱 (Q)取消？[Q]"))


class Animation(PttMenu):

    @staticmethod
    def is_entered(lines):
        bg = yield ProxyEvent.req_cursor_background
        assert bg is not None
        yield ProxyEvent.ok

        yield ProxyEvent.as_bool( \
                  (bg == "white" and lines[-1].strip() == '' and \
                   ("直接播放請輸入速度" in lines[-2] or "要模擬 24 行嗎" in lines[-2])) or \
                  "動畫播放中" in lines[-1] )


class ProxyCommand(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.true

    # The command box must only use the last line as a command may alter the content on the screen except the last line
    CommandRow = -1
    CommandCol = 60
    CommandPrompt = "Command:"
    CommandMaxLen = CommandCol - len(CommandPrompt)

    def enter(self, y, x, lines):
        yield from super().enter(y, x, lines)

        yield ProxyEvent.cut_stream(0)

        self.input = ""
        self.screenData = yield ProxyEvent.req_screen_data(ClientContext(self.CommandRow, 1, length=self.CommandCol))
        assert self.screenData is not None
        yield ProxyEvent.ok

        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1, self.CommandPrompt, fg="white", bg="black"))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8, " " * self.CommandMaxLen, fg="white", bg="black", bold=True))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8))

    def exit(self):
        yield from super().exit()
        yield ProxyEvent.send_to_client(self.screenData)
        yield ProxyEvent.reset_rendition
        yield ProxyEvent.draw_cursor
        yield ProxyEvent.resume_stream

    Commands = { "ban\s+([0-9]+)":   (lambda matched: ProxyEvent.ban_floor(int(matched.group(1)))),
                 "unban\s+([0-9]+)": (lambda matched: ProxyEvent.unban_floor(int(matched.group(1)))),
                 # for thread without explicit url
                 "ground\s+([0-9]+)": (lambda matched: ProxyEvent.set_ground(int(matched.group(1)))),
               }

    def client_event(self, event: ClientEvent):
        yield from super().client_event(event)
#        yield ProxyEvent.drop_content
        if ClientEvent.isViewable(event):
            if len(self.input) < self.CommandMaxLen:
                self.input += chr(event)
                yield ProxyEvent.event_to_client(event)  # echo

                # send current ground for convenience
                if self.input.lstrip() == "ground ":
                    ground = yield ProxyEvent.get_ground
                    assert ground is not None
                    yield ProxyEvent.ok
                    ground = str(ground)
                    if len(self.input + ground) < self.CommandMaxLen:
                        self.input += ground
                        yield ProxyEvent.draw_client(ClientContext(content=ground))
        elif event == ClientEvent.Backspace:
            if self.input:
                self.input = self.input[:-1]
                yield ProxyEvent.send_to_client(b'\b \b')
        elif event == ClientEvent.Enter:
            for cmd, event in self.Commands.items():
                matched = re.match(cmd, self.input.strip())
                if matched:
                    yield event(matched)
                    break
            yield from self.exit()

# a PTT thread being viewed
class PttThread(PttMenu):

    def __init__(self, board, key=None):
        self.board = board
        self.key = key
        super().__init__()

    def reset(self):
        super().reset()

        if self.key: self._prefix = self.keyToPrefix(self.board.name, self.key)

        self.lines = []
        self.lastLine = 0

        self.url = None
        '''
            The ground line is the base line of floor, ususally the same line where the URL is.
            The URL is scanned bottom-up first.
            To avoid collision, 'Q' can also be pressed to retrieve the correct URL and set the ground line accordingly.
            In case of no URL, command "ground <line>" can set the ground line explicitly.
        '''
        self.groundLine = 0
        self.floors = []
        self.bannedFloors = {}

        # TODO: use datetime.timedelta
        self.firstVisited = self.lastVisited = 0  # Epoch time
        self.elapsedTime = 0  # in seconds
        self.revisit = 0  # in number of revisit

        # hash value of the first screen (starts with line 1)
        self.hash_value = None

        self.atBegin = self.atEnd = False
        self.viewedFirstLine = 0
        self.viewedLastLine = 0
        self.viewedLastRow = 0
        self.floorInView = 0

    @staticmethod
    def keyToPrefix(board, key):
        # if the key doesn't start with 6 numbers and spaces, use the title string excluding the type like "[公告] "
        threadid = re.match("([0-9\s]{6})", key)
        threadid = threadid.group(1) if threadid else key[27+5:][:10]
        return f"{board}/{threadid}"

    def setKey(self, key):
        self.key = key
        self._prefix = self.keyToPrefix(self.board.name, self.key)

    def setURL(self, url: str):
        if url == self.url: return
        self.url = url
        self.groundLine = 0
        self.floors = []
        self.bannedFloors = {}
        if self.lastLine:
            self.scanURL()
            if self.groundLine:
                self.scanFloor()

    @classmethod
    def is_entered(cls, lines):
        yield ProxyEvent.as_bool(lines[-1].lstrip().startswith('瀏覽 ') and lines[-1].rstrip().endswith('(←)離開'))

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
        if hasattr(self, "switchedTime"):
            self.enteredTime = self.switchedTime
            del self.switchedTime
        else:
            self.enteredTime = time.time()
        if not self.firstVisited:
            self.firstVisited = self.enteredTime
        else:
            self.revisit += 1

        yield from self.lets_do_enteringTrigger()

        yield from super().enter(y, x, lines)
        if self.url: print(self.prefix(), "has URL", self.url)

    def switch(self, y, x, lines):
        self.switchedTime = time.time()
        yield from super().switch(y, x, lines)

        # re-enter to let the parent have the correct key
        yield ProxyEvent.cut_stream(2)
        yield ProxyEvent.event_to_server(ClientEvent.q)   # to return to the board
        # to re-enter the thread once it exited
        # don't yield directly as 'q' and 'r' almost arriving at the same time may confuse the server
        self.resume_event = ProxyEvent.event_to_server(ClientEvent.r)
        self.setEnteringTrigger(ProxyEvent.resume_stream)

    def transfer(self, from_thread):
        self.switchedTime = from_thread.switchedTime

    def exit(self):
        if not hasattr(self, "switchedTime"):
            self.lastVisited = time.time()
            elapsed = self.lastVisited - self.enteredTime
            if elapsed > 0: self.elapsedTime += elapsed
        yield from super().exit()

    def client_event(self, event: ClientEvent):
        class Commands:
            Names = ["BAN_FLOOR", "UNBAN_FLOOR", "SET_GROUND", "GET_GROUND"]
            Commands = {getattr(ProxyEvent, name): getattr(self, name.lower()) for name in Names}

            @classmethod
            def __call__(cls, event):
                # e.g. ProxyEvent.BAN_FLOOR: return self.ban_floor(cls.lets_do_it, event.content)
                return cls.Commands[event._type](cls.lets_do_it, event.content)

            @classmethod
            def watched(cls, lets_do_it):
                cls.lets_do_it = lets_do_it
                return {getattr(ProxyEvent, name): cls() for name in cls.Names}

        if isinstance(self.subMenu, ProxyCommand):
            lets_do_it = super().client_event(event)
            lets_do_exit = self.lets_do_subMenuExited(0, 0, [' '])
            yield from self.lets_do_if_return(lets_do_it, lets_do_exit, Commands.watched(lets_do_it))
        else:
            yield from super().client_event(event)
            if self.subMenu: return

            if self.is_prohibited(event):
                yield ProxyEvent.drop_content
            elif self.clientEvent == ClientEvent.x:
                yield ProxyEvent.drop_content
                yield from self.lets_do_new_subMenu(ProxyCommand, 0, 0, [' '])

    def pre_update_pre_submenu(self, y, x, lines):
        if self.floorInView:
            yield from self.lets_do_clearFloor()

    def pre_update_is_self(self, y, x, lines):
        if self.floorInView:
            yield from self.lets_do_clearFloor()

        if self.is_switch_event(self.clientEvent):
            yield from self.exit()

    # deliberate to prohibit thread switch by Up/BS at the first line, and Down/Right/Enter/Space at the last line
    # It makes little sense to me to browse thread blindly. Use those keys in is_switch_event() if desired.
    def is_prohibited(self, event: ClientEvent):
        return (event in [ClientEvent.Key_Up, ClientEvent.Backspace] and self.atBegin) or \
               (event in [ClientEvent.Key_Down, ClientEvent.Key_Right, ClientEvent.Enter, ClientEvent.Space] and self.atEnd)

    # switch to another thread
    @staticmethod
    def is_switch_event(event: ClientEvent):
        return chr(event) in "fb]+Aa"

    # move in the same thread or switch to another, status can only be determined in post_update_is_self()
    @staticmethod
    def may_be_switch_event(event: ClientEvent):
        return chr(event) in "[-=t"

    subMenus = { ClientEvent.s: SearchBoard,
                 ClientEvent.h: HelpScreen,
                 ClientEvent.QuestionMark: HelpScreen,
                 ClientEvent.o: ThreadOption,
                 ClientEvent.Backslash: ThreadOption,
                 ClientEvent.Q: ThreadInfo,
                 ClientEvent.Slash: SearchThread,
                 ClientEvent.r: UnableToReply,
                 ClientEvent.y: UnableToReply,
                 ClientEvent.p: Animation,
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

    re_match_browse_line = (lambda line: re.match("瀏覽.+\(\s*?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", line.lstrip()))

    def post_update_is_self(self, y, x, lines):
        if self.may_be_switch_event(self.clientEvent):
            browse = type(self).re_match_browse_line(lines[-1])
            if browse is None or int(browse.group(2)) == 1:
                hash_value = self.hash_screen(lines)
                if self.hash_value is None or hash_value is None or self.hash_value != hash_value:
                    yield from self.exit()
        else:
            yield from super().post_update_is_self(y, x, lines)

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        yield from self.lets_do_view(lines)

    re_push_msg = "(推|噓|→) [0-9A-Za-z]+\s*:"

    def ban_floor(self, lets_do_it, floor: int):
        if 0 < floor <= len(self.floors) and floor not in self.bannedFloors:
            line = self.floors[floor-1]
            lets_do_it = self.ban_line(line, True)  # no need to return the cursor as ProxyCommand.exit() will do
            def add_banned_floor(event):
                self.bannedFloors[floor] = event.content
#                print(self.prefix(), "banned %d: '%s'" % (floor, event.content))
            yield from self.evaluate(lets_do_it, {ProxyEvent.BANNED_LINE: add_banned_floor})

    def unban_floor(self, lets_do_it, floor: int, delete: bool = True):
        def line_width(text):
            return sum([(2 if ord(c) > 0xff else 1) for c in text])

        if floor <= 0 or floor not in self.bannedFloors: return
#        print(self.prefix(), "unbanned %d: '%s'" % (floor, self.bannedFloors[floor]))
        line = self.floors[floor-1]
        if self.viewedFirstLine <= line <= self.viewedLastLine:
            prefix = re.match(self.re_push_msg + "\s", self.lines[line-1])
            row = self.viewedLastRow - (self.viewedLastLine - line)
            col = line_width(prefix.group(0)) + 1
            content = self.bannedFloors[floor]
            yield ProxyEvent.draw_client(ClientContext(row, col, content, fg="brown"))
            yield ProxyEvent.reset_rendition
        if delete: del self.bannedFloors[floor]

    def set_ground(self, lets_do_it, line: int):
        if self.groundLine == line or line < 0 or line > self.lastLine: return
        for floor in self.bannedFloors:
            yield from self.unban_floor(lets_do_it, floor, False)
        self.bannedFloors = {}

        if self.floorInView:
            yield from self.lets_do_clearFloor()

        # TODO: can the ground line below the URL even the floors are correct in both cases?
        self.groundLine = line
        self.floors = []
        yield from self.lets_do_update()

    def get_ground(self, lets_do_it, none = None):
        lets_do_it.send(self.groundLine if self.groundLine else self.viewedFirstLine)

    def ban_line(self, line: int, yield_line: bool = False, cursor_return: bool = False):
        def line_width(text):
            return sum([(2 if ord(c) > 0xff else 1) for c in text])

        if line < 0 or line > self.lastLine or self.lines[line-1] == self.LINE_HOLDER or \
           line not in self.floors: return

        prefix = re.match(self.re_push_msg + "\s", self.lines[line-1])
#            print(self.prefix(), "ban_line: prefix: '%s'" % prefix.group(0))
        suffix = re.search("(\s+([0-9.]+\s)?[0-9/]+\s[0-9:]+\s*)$", self.lines[line-1])
#            print(self.prefix(), "ban_line: suffix: '%s'" % suffix.group(0))
        banned = self.lines[line-1][len(prefix.group(0)) : - len(suffix.group(0))]
#            print(self.prefix(), "ban_line: banned: '%s'" % banned)
        if yield_line: yield ProxyEvent.banned_line(banned)
        if self.viewedFirstLine <= line <= self.viewedLastLine:
            row = self.viewedLastRow - (self.viewedLastLine - line)
            col = line_width(prefix.group(0)) + 1
            content = '-' * line_width(banned)
            yield ProxyEvent.draw_client(ClientContext(row, col, content, fg="blue"))
            yield ProxyEvent.reset_rendition
            if cursor_return: yield ProxyEvent.draw_cursor

    @classmethod
    def hash_screen(cls, lines):
        non_empty = []
        row = 0
        stop_row = len(lines) - 1
        # usually the first thress lines are author, title and time
        while row < stop_row and len(non_empty) < 3:
            if lines[row].strip(): non_empty.append(str(row+1) + lines[row].strip())
            row += 1
        # add the last non-empty row
        stop_row, row = row-1, stop_row-1
        while stop_row < row and len(non_empty) < 4:
            if lines[row].strip(): non_empty.append(str(row+1) + lines[row].strip())
            row -= 1
        if len(non_empty) == 4:
            hash_value = hash(tuple(non_empty))
#            print(hash_value, tuple(non_empty))
            return hash_value
        return None

    LINE_HOLDER = chr(0x7f)

    def lets_do_view(self, lines):
        browse = type(self).re_match_browse_line(lines[-1])
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

        if self.atBegin and self.hash_value is None:
            self.hash_value = self.hash_screen(screenLines)

        if self.lastLine < last:
            # which is better?
            #   [self.LINE_HOLDER for _ in range(last - self.lastLine)]
            #   self.LINE_HOLDER * (last - self.lastLine)
            self.lines.extend(self.LINE_HOLDER * (last - self.lastLine))
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
        self.viewedLastRow = i

        yield from self.lets_do_update()

    def lets_do_update(self):
        if not self.groundLine: self.scanURL()

        if not self.groundLine: return
        self.scanFloor()

        if not self.floors: return
        yield from self.lets_do_showFloor(self.viewedFirstLine, self.viewedLastLine, self.viewedLastRow)

        if not self.floorInView: return
        yield from self.lets_do_banFloor(self.viewedFirstLine, self.viewedLastLine)

    def lets_do_banFloor(self, firstLine, lastLine):
        if not self.floorInView: return
        banned = False
        firstLine = max(firstLine, self.groundLine+1)
        for floor in self.bannedFloors:
            line = self.floors[floor-1]
            if firstLine <= line <= lastLine:
                banned = True
                yield from self.ban_line(line)
        if banned:
            yield ProxyEvent.draw_cursor

    def firstFloorInView(self, firstLine, lastLine):
        if not self.floors or self.floors[-1] < firstLine: return 0
        floor = 1
        while self.floors[floor-1] < firstLine: floor += 1
        return floor if self.floors[floor-1] <= lastLine else 0

    minColumns = 86
    maxWidth = 5

    def lets_do_showFloor(self, firstLine, lastLine, lastRow):
        self.floorInView = 0
        if not self.groundLine: return

        columns = yield ProxyEvent.req_screen_column
        assert columns is not None
        yield ProxyEvent.ok
        if columns < self.minColumns: return

        floor = self.firstFloorInView(firstLine, lastLine)
#        print(self.prefix(), "firstFloorInView(%d, %d) = %d" % (firstLine, lastLine, floor))
        if not floor: return

        self.floorInView = floor
        floors = len(self.floors)
        while floor <= floors and self.floors[floor-1] <= lastLine:
            row = lastRow - (lastLine - self.floors[floor-1])
            col = self.minColumns + 1 - self.maxWidth
            content = "{:^{width}}".format(floor, width = self.maxWidth)
            yield ProxyEvent.draw_client(ClientContext(row, col, content))
            floor += 1
        yield ProxyEvent.draw_cursor

    def lets_do_clearFloor(self):
        if not self.floorInView: return
        lastLine = self.viewedLastLine
        lastRow = self.viewedLastRow
        floor = self.floorInView
        floors = len(self.floors)
        while floor <= floors and self.floors[floor-1] <= lastLine:
            row = lastRow - (lastLine - self.floors[floor-1])
            col = self.minColumns + 1 - self.maxWidth
            content = ' ' * self.maxWidth
#            print(self.prefix(), f"clear floor [{row:2}, {col:2}] = '{content}'")
            yield ProxyEvent.draw_client(ClientContext(row, col, content))
            floor += 1
        self.floorInView = 0

    def scanFloor(self):
        if not self.groundLine: return
        line = self.floors[-1] if self.floors else self.groundLine
        while line < self.lastLine:
            if self.lines[line] == self.LINE_HOLDER: return
            if re.match(self.re_push_msg, self.lines[line]):
                self.floors.append(line+1)
#                print("line:", line+1, "floor:", len(self.floors))
            line += 1

    def scanURL(self):
        if self.lastLine < 3:
            return None
        if self.url and self.groundLine:
            return self.url

        if self.url:
            # top-down as we are confident what the URL is
            i = 2
            while i < self.lastLine - 2:
                if self.lines[i].startswith("※ 發信站: 批踢踢實業坊") and \
                   self.lines[i+1].startswith("※ 文章網址:") and \
                  (self.lines[i+1])[7:].strip() == self.url:
                    self.groundLine = (i+1)+1
                    print("scanURL top-down:", self.url, "at", self.groundLine)
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
                    self.groundLine = (i+2)+1
                    print("scanURL bottom-up:", self.url, "at", self.groundLine)
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
        # TODO: use datetime.timedelta
        def sec2time(seconds):
            add_time = (lambda _str, _time: (_str + ' ').lstrip() + _time)
            time_str = ""
            hours, seconds = divmod(seconds, 3600)
            if hours: time_str = add_time(time_str, "%d hr" % hours)
            minutes, seconds = divmod(seconds, 60)
            if minutes: time_str = add_time(time_str, "%d min" % minutes)
            return add_time(time_str , "%d sec" % seconds)

        url = self.scanURL()
        print("\nThread lines:", self.lastLine, "url:", url)
        if self.firstVisited: print("firstVisited:", time.ctime(self.firstVisited))
        if self.lastVisited:  print("lastVisited:", time.ctime(self.lastVisited))
        print("Elapsed:", sec2time(round(self.elapsedTime)))
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
        if not hasattr(self, "groundLine"): self.groundLine = 0

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

        if self.firstVisited == 0:
            self.firstVisited = thread.firstVisited
        self.lastVisited = thread.lastVisited
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

    thread.elapsedTime = 51452.3456
    thread.show()
    print("floors:", thread.floors)

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

