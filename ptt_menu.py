from abc import ABC, abstractmethod

from ptt_event import ProxyEvent, ClientEvent

class PttMenuTemplate:

    def __init__(self):
        self.reset()

    def reset(self):
        self._prefix = type(self).__qualname__
        self.clientEvent = ClientEvent.Unknown
        self.exited = False

    def prefix(self):
        return f"In {self._prefix}:"

    def enter(self, y, x, lines):
        print(self.prefix(), "entered")
        self.exited = False
        if False: yield

    def exit(self):
        print(self.prefix(), "exited, last client event:", ClientEvent.name(self.clientEvent))
        self.clientEvent = ClientEvent.Unknown
        self.exited = True
        yield ProxyEvent(ProxyEvent.RETURN, self._prefix)

    def client_event(self, event: ClientEvent):
        print(self.prefix(), ClientEvent.name(event))
        self.clientEvent = event
        if False: yield

    def pre_update(self, y, x, lines):
        if False: yield

    def post_update(self, y, x, lines):
        self.clientEvent = ClientEvent.Unknown
        if False: yield


class PttMenu(PttMenuTemplate, ABC):

    def reset(self):
        super().reset()
        self.subMenu = None

    def client_event(self, event: ClientEvent):
        if self.subMenu:
            yield from self.subMenu.client_event(event)
        else:
            yield from super().client_event(event)

    def pre_update_submenu(self, y, x, lines):
        if self.subMenu:
            yield from self.subMenu.pre_update(y, x, lines)

    def pre_update_self(self, y, x, lines):
        if False: yield

    def pre_update(self, y, x, lines):
        done = False
        for event in self.pre_update_submenu(y, x, lines):
            if event._type == ProxyEvent.DONE:
                done = True
            else:
                yield event
        if done: return
        for event in self.pre_update_self(y, x, lines):
            if event._type == ProxyEvent.DONE:
                done = True
            else:
                yield event
        if done: return
        yield from super().pre_update(y, x, lines)

    def isSubMenuEntered(self, menu, lines):
        return ProxyEvent.eval_bool(menu.is_entered(lines))

    def makeSubMenu(self, menu):
        return menu()

    def subMenuEntered(self):
        self.clientEvent = ClientEvent.Unknown

    def post_update_is_submenu(self, y, x, lines):
        assert self.subMenu is None

        if self.clientEvent in getattr(self, "subMenus", {}):
            menu = self.subMenus[self.clientEvent]
            if self.isSubMenuEntered(menu, lines):
                self.subMenu = self.makeSubMenu(menu)
                self.subMenuEntered()
                yield from self.subMenu.enter(y, x, lines)
                # don't issue DONE to preserve the last client event, otherwise the event will match the subMenu again
                #yield ProxyEvent(ProxyEvent.DONE)

    # at this point, self state is still unknown. (e.g. searching board in a thread could jump to another board)
    def subMenuExited(self, y, x, lines):
        self.subMenu = None

    def post_update_submenu(self, y, x, lines):
        assert self.subMenu is not None

        quitMenu = False
        for event in self.subMenu.post_update(y, x, lines):
            if event._type == ProxyEvent.RETURN:
                quitMenu = True
            else:
                yield event

        if quitMenu:
            self.subMenuExited(y, x, lines)

    def post_update_is_self(self, y, x, lines):
        assert self.subMenu is None

        if not ProxyEvent.eval_bool(self.is_entered(lines)):
            yield from self.exit()

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if False: yield

    def post_update(self, y, x, lines):
        returnFromSubMenu = False
        if self.subMenu is None:
            yield from self.post_update_is_submenu(y, x, lines)
        else:
            yield from self.post_update_submenu(y, x, lines)
            returnFromSubMenu = self.subMenu is None
        if self.subMenu: return

        yield from self.post_update_is_self(y, x, lines)
        if self.exited: return

        yield from self.post_update_self(returnFromSubMenu, y, x, lines)

        # TODO: necessary or not?
        yield from super().post_update(y, x, lines)

    @staticmethod
    @abstractmethod
    def is_entered(lines):
        ...


class HelpScreen(PttMenu):

    @staticmethod
    def is_entered(lines):
        if "請按 空白鍵 繼續" not in lines[-1]:
            yield ProxyEvent.as_bool(False)
            return
        # in panel, board and thread
        yield ProxyEvent.as_bool( \
                  lines[0].startswith("【 看板選單輔助說明 】") or \
                  lines[0].startswith("【基本命令】") or \
                  ("瀏覽程式使用說明" in lines[0]) )

    class CallAngel(PttMenu):

        @staticmethod
        def is_entered(lines):
            yield ProxyEvent.as_bool(
                      lines[0].startswith("問歐買尬小天使:") or
                      lines[0].startswith("【小天使留言】") )

    subMenus = { ClientEvent.h: CallAngel }


class SearchBoard(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool(
                  (lines[0].startswith("【 搜尋全站看板 】") or \
                   lines[0].startswith("【 選擇看板 】")) and \
                  lines[1].startswith("請輸入看板名稱") )


class ThreadInfo(PttMenu):

    @staticmethod
    def is_entered(lines):
        if "請按任意鍵繼續" not in lines[-1]:
            yield ProxyEvent.as_bool(False)
            return
        for i in range(2, len(lines)-4):   # the box spans at least 4 lines
            if lines[i].startswith("│ 文章代碼(AID):") and \
               lines[i+1].startswith("│ 文章網址:"):
                url = (lines[i+1])[7:].strip(" │")
                yield ProxyEvent(ProxyEvent.THREAD_URL, url)
                yield ProxyEvent.as_bool(True)
                return
        yield ProxyEvent.as_bool(False)


def test(menu):
    lines = [""]*10
    for event in menu.is_entered(lines):
        print(event)
    for event in menu.enter(0, 0, lines):
        print(event)
    for event in menu.client_event(ClientEvent.Key_Space):
        print(event)
    for event in menu.pre_update(0, 0, lines):
        print(event)
    for event in menu.post_update(0, 0, lines):
        print(event)
    # exit() will be called in post_update() as the lines are blank
    #for event in info.exit():
    #    print(event)

if __name__ == "__main__":
    test(HelpScreen())

