class UserEvent:
    Unknown = 0
    Key_Up = 1
    Key_Down = 2
    Key_Right = 3
    Key_Left = 4
    Key_Enter = 5
    Key_PgUp = 6
    Key_PgDn = 7
    Key_Home = 8
    Key_End = 9
    Key_Backspace = 10

    # 32 ~ 126 is the same viewable ASCII code
    Key_Space = ord(' ')

    @staticmethod
    def isViewable(event: int):
        return ' ' <= chr(event) <= '~'

    @classmethod
    def name(cls, event: int):
        assert event >= 0
        return ["-", "Up", "Down", "Right", "Left", "Enter", "PgUp", "PgDn",
                "Home", "End", "BS"][event] if event <= cls.Key_Backspace else f"'{chr(event)}'"


