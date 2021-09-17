from dataclasses import dataclass


class UserEvent:
    Unknown = 0

    Backspace = ord('\b')
    Enter = ord('\r')
    Tab = ord('\t')

    # 32 ~ 126 is the same viewable ASCII code
    Space  = ord(' ')
    Colon  = ord(':')
    SemiColon = ord(';')

    Key_Up    = 0x101
    Key_Down  = 0x102
    Key_Right = 0x103
    Key_Left  = 0x104
    Key_PgUp  = 0x105
    Key_PgDn  = 0x106
    Key_Home  = 0x107
    Key_End   = 0x108

    @staticmethod
    def isViewable(event: int):
        return ' ' <= chr(event) <= '~'

    @classmethod
    def name(cls, event: int):
        assert event >= 0
        if cls.isViewable(event):
            return f"'{chr(event)}'"
        elif cls.Key_Up <= event <= cls.Key_End:
            return ["Up", "Down", "Right", "Left", "PgUp", "PgDn", "Home", "End"][event - cls.Key_Up]
        else:
            return cls.to_bytes(event)

    @staticmethod
    def to_bytes(event: int):
        return event.to_bytes(1, 'big')

# Ctrl_A ~ Ctrl_Z
for a in range(1, 0x1a+1):
    setattr(UserEvent, 'Ctrl_'+chr(a-1+ord('A')), a)
# Key0 ~ Key9
for d in range(ord('0'), ord('9')+1):
    setattr(UserEvent, 'Key'+chr(d), d)
# A ~ Z
for A in range(ord('A'), ord('Z')+1):
    setattr(UserEvent, chr(A), A)
# a - z
for a in range(ord('a'), ord('z')+1):
    setattr(UserEvent, chr(a), a)


@dataclass
class ProxyEvent:
    _type: int
    content: bytes = None

    # class methods

    WARNING = -1

    FALSE = 0
    TRUE  = 1

    # data stream events
    CUT_STREAM    = 0x80     # cut stream between server and client, only feed to the virtual terminal
    RESUME_STREAM = 0x81     # resume stream

    # message content events
    DROP_CONTENT     = 0x90
    REPLACE_CONTENT  = 0x91
    INSERT_TO_CLIENT = 0x92
    SEND_TO_CLIENT   = 0x93
    INSERT_TO_SERVER = 0x94
    SEND_TO_SERVER   = 0x95

    # terminal events
    TERMINAL_START = 0x100
    RETURN = 0x101      # menu return
    BOARD_NAME = 0x102
    THREAD_URL = 0x103

    no_arguments = { "FALSE", "TRUE", "CUT_STREAM", "RESUME_STREAM", "DROP_CONTENT" }

    type2names = {}

    def __repr__(self):
        if self._type in self.type2names:
            type = self.type2names[self._type]
        else:
            type = hex(self._type)
        return "event(" + type + ", " + repr(self.content) + ")"

    @classmethod
    def as_bool(cls, value: bool):
        return cls(cls.TRUE if value else cls.FALSE)

    @classmethod
    def eval_bool(cls, lets_do_it):
        result = None
        for event in lets_do_it:
            if event is True or event is False:
                result = event
            elif event._type == cls.TRUE:
                result = True
            elif event._type == cls.FALSE:
                result = False
        return result

    @classmethod
    def eval_type(cls, lets_do_it, _type):
        result = None
        for event in lets_do_it:
            if event._type == _type:
                result = event.content
        return result

    @classmethod
    def event_to_server(cls, event: int):
        return cls(cls.SEND_TO_SERVER, UserEvent.to_bytes(event))

ProxyEvent.type2names = {getattr(ProxyEvent, name):name for name in dir(ProxyEvent) if 'A' <= name[0] <= 'Z'}

# make shortcuts like: ProxyEvent.cut_stream = ProxyEvent(ProxyEvent.CUT_STREAM)
for name in ProxyEvent.no_arguments:
    setattr(ProxyEvent, name.lower(), ProxyEvent(getattr(ProxyEvent, name)))

# make shortcuts with content
for name in ProxyEvent.type2names.values():
    if name not in ProxyEvent.no_arguments:
        # doesn't work as expected because the lambda (and the name in its body) is not eagerly evaluated.
        # at the time it's evaluated outside this loop, it will be the last value in ProxyEvent.type2names (as a closure)
        #setattr(ProxyEvent, name.lower(), lambda content: ProxyEvent(getattr(ProxyEvent, name), content))
        #setattr(ProxyEvent, name.lower(), lambda content: ProxyEvent(getattr(ProxyEvent, f'{name}'), content))

        # corret
        setattr(ProxyEvent, name.lower(), eval(f"lambda content: ProxyEvent(getattr(ProxyEvent, '{name}'), content)"))

# 'return' is a keyword
ProxyEvent._return = lambda content: ProxyEvent(ProxyEvent.RETURN, content)

@dataclass
class ProxyEventTrigger:
    _type: int
    event: ProxyEvent


ClientEvent = UserEvent


if __name__ == "__main__":
    print(ProxyEvent.as_bool(True))
    print(ProxyEvent.cut_stream)
    print(ProxyEvent.replace_content(b'112233'))
    print(ProxyEvent._return("3434343"))


