from abc import ABC, abstractmethod

from ptt_event import ProxyEvent, ClientEvent, ProxyEventTrigger

class PttMenuTemplate:

    event_trigger: ProxyEventTrigger

    def __init__(self):
        self.reset()

    def reset(self):
        self._prefix = type(self).__qualname__
        self.clientEvent = ClientEvent.Unknown
        self.exited = False
        self.event_trigger = None
        self.resume_event = None    # to be checked by the parent

    def __repr__(self):
        return self._prefix

    def prefix(self):
        return f"In {self._prefix}:"

    # entered from a parent
    def enter(self, y, x, lines):
        print(self.prefix(), "entered")
        self.exited = False
        self.event_trigger = None
        self.resume_event = None
        if False: yield

    # switched from a sibling
    def switch(self, y, x, lines):
        print(self.prefix(), "switched")
        self.exited = False
        self.event_trigger = None
        self.resume_event = None
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

    def to_be_resumed(self):
        return self.resume_event

    def evaluate(self, lets_do_it):
        if self.event_trigger:
            triggered = False
            for event in lets_do_it:
                if event._type == self.event_trigger._type:
                    triggered = True
                    yield self.event_trigger.event
                yield event
            if triggered:
                self.event_trigger = None
        else:
            yield from lets_do_it

class PttMenu(PttMenuTemplate, ABC):

    def reset(self):
        super().reset()
        self.subMenu = None
        self.__subMenuExited = False

    def client_event(self, event: ClientEvent):
        if self.subMenu:
            yield from self.subMenu.client_event(event)
        else:
            yield from super().client_event(event)

    def pre_update_submenu(self, y, x, lines):
        assert self.subMenu is not None

        quitMenu = False
        for event in self.subMenu.pre_update(y, x, lines):
            if event._type == ProxyEvent.RETURN:
                quitMenu = True
            else:
                yield event

        if quitMenu:
            yield from self.lets_do_subMenuExited(y, x, lines)

    def pre_update_is_self(self, y, x, lines):
        if False: yield

    def pre_update_self(self, y, x, lines):
        if False: yield

    def pre_update(self, y, x, lines):
        self.__subMenuExited = False
        if self.subMenu:
            yield from self.pre_update_submenu(y, x, lines)
            #if self.subMenu: return
            if self.subMenu is None:
                self.__subMenuExited = True
            # TODO: should return anyway regardless of self.subMenu?
            return

        if self.clientEvent not in getattr(self, "subMenus", {}):
            yield from self.pre_update_is_self(y, x, lines)
            if self.exited: return

        yield from self.pre_update_self(y, x, lines)
        yield from super().pre_update(y, x, lines)

    def isSubMenuEntered(self, menu, lines):
        yield from menu.is_entered(lines)

    def makeSubMenu(self, menu):
        return menu()

    def subMenuEntered(self):
        # to prevent the event matches subMenus again once returned
        self.clientEvent = ClientEvent.Unknown
        if False: yield

    def lets_do_new_subMenu(self, menu, y, x, lines):
        if self.subMenu is None:
            self.subMenu = self.makeSubMenu(menu)
        yield from self.subMenuEntered()
        yield from self.subMenu.enter(y, x, lines)
        yield from self.subMenu.post_update_self(False, y, x, lines)

    def post_update_is_submenu(self, y, x, lines):
        assert self.subMenu is None

        if self.clientEvent in getattr(self, "subMenus", {}):
            menu = self.subMenus[self.clientEvent]
            entered = False
            for event in self.isSubMenuEntered(menu, lines):
                if (event is True) or (event is False):
                    entered = event
                elif event._type == ProxyEvent.TRUE or event._type == ProxyEvent.FALSE:
                    entered = (event._type == ProxyEvent.TRUE)
                else:
                    yield event
            if entered:
                yield from self.lets_do_new_subMenu(menu, y, x, lines)

    # at this point, self state is still unknown.
    # e.g. searching board in a thread could jump to another board without returning to the parent.
    # the parent board only knows the thread exited but is unsure if it returns to itself until post_update_is_self().
    def lets_do_subMenuExited(self, y, x, lines):
        self.subMenu = None
        if False: yield

    def post_update_submenu(self, y, x, lines):
        assert self.subMenu is not None

        quitMenu = False
        for event in self.subMenu.post_update(y, x, lines):
            if event._type == ProxyEvent.RETURN:
                quitMenu = True
            else:
                yield event

        if quitMenu:
            yield from self.lets_do_subMenuExited(y, x, lines)

    def post_update_is_self(self, y, x, lines):
        assert self.subMenu is None

        if not ProxyEvent.eval_bool(self.is_entered(lines)):
            yield from self.exit()

    def post_update_self(self, returnFromSubMenu, y, x, lines):
        if False: yield

    def post_update(self, y, x, lines):
        if not self.__subMenuExited:
            if self.subMenu is None:
                if self.clientEvent != ClientEvent.Unknown:
                    yield from self.post_update_is_submenu(y, x, lines)
            else:
                yield from self.post_update_submenu(y, x, lines)
                self.__subMenuExited = self.subMenu is None
            if self.subMenu: return

        yield from self.post_update_is_self(y, x, lines)
        if self.subMenu or self.exited: return

        yield from self.post_update_self(self.__subMenuExited, y, x, lines)

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


class QuickSwitch(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.as_bool(lines[-1].startswith(" ★快速切換:"))


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
    for event in menu.client_event(ClientEvent.Space):
        print(event)
    for event in menu.pre_update(0, 0, lines):
        print(event)
    menu.event_trigger = ProxyEventTrigger(ProxyEvent.RETURN, ProxyEvent.event_to_server(ClientEvent.Space))
    for event in menu.evaluate(menu.post_update(0, 0, lines)):
        print(event)
    # exit() will be called in post_update() as the lines are blank
    #for event in info.exit():
    #    print(event)

if __name__ == "__main__":
    test(HelpScreen())

