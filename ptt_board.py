from user_event import ProxyEvent, UserEvent

class PttBoard:

    def __init__(self, name):
        self.name = name
        self.clientEvent = UserEvent.Unknown
        self.subState = None

    def prefix(self, child=""):
        return f"In {self.name}" + (f"({child})" if child else "") + ":"

    def client_event(self, event:UserEvent):
        print(self.prefix(), UserEvent.name(event))
        self.clientEvent = event

        if self.subState:
            for ev in self.subState(self, event):
                if ev._type == ProxyEvent.RETURN:
                    print("return to board", self.name)
                elif ev._type == ProxyEvent.SWITCH:
                    print("switch from board", self.name)
                    yield ProxyEvent(ProxyEvent.SWITCH, self.name)
                else:
                    print("from", self.eventHandler, "issue", ev)
                self.eventHandler = None
                self.clientEvent = UserEvent.Unknown

        if False: yield

    def searchBoard(self, event:UserEvent = None):
        print(self.prefix("searchBoard"), UserEvent.name(event) if event else "enter")
        if event == UserEvent.Key_Enter:
            yield ProxyEvent(ProxyEvent.SWITCH)

    def helpScreen(self, event:UserEvent = None):
        print(self.prefix("helpScreen"), UserEvent.name(event) if event else "enter")
        if event is not None:
            yield ProxyEvent(ProxyEvent.RETURN)

    client_event_handlers = { UserEvent.s: searchBoard, UserEvent.h: helpScreen }

    def pre_update(self):
        print("board.pre_update:", self.subState, UserEvent.name(self.clientEvent))
        if self.subState is None and self.clientEvent in self.client_event_handlers:
            self.subState = self.client_event_handlers[self.clientEvent]
            yield from self.subState(self)

    def post_update(self):
        if False: yield

