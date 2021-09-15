from abc import ABC, abstractmethod

from ptt_event import ProxyEvent, ClientEvent

class PttMenu:

    def __init__(self):
        self.reset()

    def reset(self):
        self._prefix = type(self).__qualname__
        self.clientEvent = ClientEvent.Unknown

    def prefix(self):
        return f"In {self._prefix}:"

    def enter(self):
        print(self.prefix(), "entered")
        if False: yield

    def leave(self):
        print(self.prefix(), "left")
        self.clientEvent = ClientEvent.Unknown
        yield ProxyEvent(ProxyEvent.RETURN)

    def client_event(self, event: ClientEvent):
        print(self.prefix(), ClientEvent.name(event))
        self.clientEvent = event
        if False: yield

    def pre_update(self, y, x, line):
        if False: yield

    def post_update(self, y, x, lines):
        self.clientEvent = ClientEvent.Unknown
        if False: yield


class PttListMenu(PttMenu, ABC):

    def client_event(self, event: ClientEvent):
        subMenu = getattr(self, "subMenu", None)
        if subMenu:
            for ev in subMenu.client_event(event):
                pass
            return
        yield from super().client_event(event)

    def pre_update_submenu(self, y, x, line):
        if False: yield

    def pre_update_self(self, y, x, line):
        if False: yield

    def pre_update(self, y, x, line):
        for event in self.pre_update_submenu(y, x, line):
            if event._type == ProxyEvent.DONE:
                return
            else:
                yield event
        for event in self.pre_update_self(y, x, line):
            if event._type == ProxyEvent.DONE:
                return
            else:
                yield event
        yield from super().pre_update(y, x, line)

    def post_update_submenu(self, y, x, lines):
        if False: yield

    def post_update_self(self, y, x, lines):
        if False: yield

    def post_update(self, y, x, lines):
        for event in self.post_update_submenu(y, x, lines):
            if event._type == ProxyEvent.DONE:
                return
            else:
                yield event
        for event in self.post_update_self(y, x, lines):
            if event._type == ProxyEvent.DONE:
                return
            else:
                yield event
        yield from super().post_update(y, x, lines)

    @classmethod
    @abstractmethod
    def is_entered(cls, lines):
        ...

class SearchBoard(PttListMenu):

    def post_update_self(self, y, x, lines):
        if not self.is_entered(lines):
            yield from self.leave()
            yield ProxyEvent(ProxyEvent.SWITCH)

    @classmethod
    def is_entered(cls, lines):
        return (lines[0].startswith("【 搜尋全站看板 】") or \
                lines[0].startswith("【 選擇看板 】")) and \
               lines[1].startswith("請輸入看板名稱")

class HelpScreen(PttListMenu):

    def post_update_self(self, y, x, lines):
        if not self.is_entered(lines):
            yield from self.leave()

    @classmethod
    def is_entered(cls, lines):
        # in panel, board and thread
        return (lines[0].startswith("【 看板選單輔助說明 】") or \
                lines[0].startswith("【基本命令】") or \
                "瀏覽程式使用說明" in lines[0]) and \
               "請按 空白鍵 繼續" in lines[-1]

if __name__ == "__main__":
    help = HelpScreen()
    for event in help.client_event(ClientEvent.Key_Space):
        print(event)
    help.is_entered([""])

