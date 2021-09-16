from dataclasses import dataclass

@dataclass
class ProxyEvent:
    FALSE = 0
    TRUE  = 1

    # message content events
    DROP_CONTENT = 10
    REPLACE_CONTENT = 11
    INSERT_TO_CLIENT = 12
    SEND_TO_CLIENT   = 13
    INSERT_TO_SERVER = 14
    SEND_TO_SERVER   = 15

    # terminal events
    TERMINAL_START = 100
    DONE  = 101
    RETURN = 102
    BOARD_NAME = 103
    THREAD_URL = 104

    _type: int
    content: bytes = None

    @classmethod
    def as_bool(cls, value: bool):
        return cls(cls.TRUE if value else cls.FALSE)

    @classmethod
    def eval_bool(cls, handler):
        result = None
        for event in handler:
            if event is True or event is False:
                result = event
            elif event._type == cls.TRUE:
                result = True
            elif event._type == cls.FALSE:
                result = False
        return result

    @classmethod
    def eval_type(cls, handler, _type):
        result = None
        for event in handler:
            if event._type == _type:
                result = event.content
        return result


class UserEvent:
    Unknown = 0

    Ctrl_B = 2
    Ctrl_F = 6
    Ctrl_Z = 0x1a

    Key_Backspace = ord('\b')
    Key_Enter = ord('\r')

    # 32 ~ 126 is the same viewable ASCII code
    Key_Space = ord(' ')
    Key0 = ord('0')
    Key1 = ord('1')
    Key2 = ord('2')
    Key3 = ord('3')
    Key4 = ord('4')
    Key5 = ord('5')
    Key6 = ord('6')
    Key7 = ord('7')
    Key8 = ord('8')
    Key9 = ord('9')
    Q = ord('Q')
    h = ord('h')
    l = ord('l')
    o = ord('o')
    q = ord('q')
    r = ord('r')
    s = ord('s')

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
            return event.to_bytes(1, 'big')

ClientEvent = UserEvent

