from dataclasses import dataclass

@dataclass
class ProxyEvent:
    TEST = 0
    DROP = 1
    REPLACE = 2
    INSERT_TO_CLIENT = 11
    SEND_TO_CLIENT   = 12
    INSERT_TO_SERVER = 13
    SEND_TO_SERVER   = 14

    IN_BOARD  = 20
    OUT_BOARD = 21
    IN_THREAD  = 30
    OUT_THREAD = 31
    RETURN = 40
    SWITCH = 41

    _type: int
    content: bytes = None

class UserEvent:
    Unknown = 0

    Ctrl_B = 2
    Ctrl_F = 6

    Key_Backspace = ord('\b')
    Key_Enter = ord('\r')

    # 32 ~ 126 is the same viewable ASCII code
    Key_Space = ord(' ')
    Q = ord('Q')
    h = ord('h')
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


