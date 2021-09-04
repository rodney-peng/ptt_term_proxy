import os
import re
import time

from uao import register_uao
register_uao()

from user_event import UserEvent

# a PTT thread being viewed
class PttThread:

    def __init__(self, filename=None):
        self.clear()

        if filename and self.loadContent(filename):
            self.scanURL()
            print("read from ", filename, "lines", self.lastLine, "url:", self.url)

    def clear(self):
        self.lines = []
        self.lastLine = 0
        self.url = None
        self.atBegin = self.atEnd = False
        self.firstViewed = self.lastViewed = 0
        self.elapsedTime = 0

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

    LINE_HOLDER = chr(0x7f)

    def saveContent(self, filename):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                for line in self.lines:
                    f.write((line if line != self.LINE_HOLDER else '') + '\n')
                print("Write", filename, "bytes", f.tell())
        except Exception as e:
            print(f"Failed to save {filename}:\n", e)

    def view(self, lines, first: int, last: int, atEnd: bool):
        assert 0 < first <= last
        assert last - first + 1 <= len(lines)

        self.atBegin = (first == 1)
        self.atEnd = atEnd

        if self.firstViewed == 0: self.firstViewed = time.time()

        if self.lastLine < last:
            # which is better?
            #   [self.LINE_HOLDER for _ in range(last - self.lastLine)]
            #   self.LINE_HOLDER * (last - self.lastLine)
            self.lines.extend(self.LINE_HOLDER * (last - self.lastLine))
            self.lastLine = last

#        print("View lines:", first, last, "curr:", len(self.lines), self.lastLine)

        i = 0
        text = ""
        while i < len(lines) and first <= last:
            line = lines[i].rstrip()
            if len(line.encode("big5uao", "replace")) > 78 and line[-1] == '\\':
                text += line[0:-1]
            else:
                self.lines[first-1] = text + line
#                print("add [%d]" % first, "'%s'" % self.lines[first-1])
                text = ""
                first += 1
            i += 1

        if text and first <= last:
            self.lines[first-1] = text
#            print("add [%d]" % first, "'%s'" % self.lines[first-1])
            first += 1

        if first <= last:
            print("\nCaution: line wrap is probably missing!\n")

    def text(self, first = 1, last = -1):
#        print("text:", first, last, self.lastLine)
        if first < 0: first = self.lastLine + 1 + first
        if last < 0: last = self.lastLine + 1 + last
#        print("text:", first, last, self.lastLine)

        text = ""
        while 0 < first <= last <= self.lastLine:
#            print("line [%d]" % first, "'%s'" % self.lines[first-1])
            text += (self.lines[first-1] + '\n')
            first += 1
        return text

    def scanURL(self):
        if self.lastLine < 3:
            return None
        if self.url:
            return self.url

        i = self.lastLine - 3
        while i > 0:
            if self.lines[i] == "--" and \
               self.lines[i+1].startswith("※ 發信站: 批踢踢實業坊") and \
               self.lines[i+2].startswith("※ 文章網址:"):
                self.url = (self.lines[i+2])[7:].strip()
                return self.url
            i -= 1

        return None

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

    # don't switch thread by Up/Down at first/last line
    def is_prohibited(self, event: UserEvent):
        return (event == UserEvent.Key_Up and self.atBegin) or \
               (event in [UserEvent.Key_Down, UserEvent.Key_Enter, UserEvent.Key_Space] and self.atEnd)

    def enablePersistence(self, enabled=True):
        self.persistence = enabled

    def switch(self, pickler):
        if self.lastLine == 0: return False
        assert self.firstViewed > 0

        self.lastViewed = time.time()
        elapsed = self.lastViewed - self.firstViewed
        if elapsed > 0: self.elapsedTime = elapsed

        if not hasattr(self, "persistence") or self.persistence:
            pickler(self)

        self.clear()
        return True

    def isSwitchEvent(self, event: UserEvent):
        return (event is not None) and \
               (not self.is_prohibited(event)) and ( \
               (event == UserEvent.Key_Left) or \
               (chr(event) in "qfb]+[-=tAa") )

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

# A PttThread object will be sent to the persistence server through normal pickling,
# then the object is merged to a PttThreadPersist object for persistence.
# A PttThreadPersist object is the accumulated status of the same PttThread objects.
class PttThreadPersist(PttThread):

    # https://docs.python.org/3/library/pickle.html#handling-stateful-objects
    # called upon pickling
    def __getstate__(self):
        state = self.__dict__.copy()
        del state['lines']
        return state

    # called upon construction or unpickling
    def __setstate__(self, state):
        self.__dict__.update(state)
        self.lines = []

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


