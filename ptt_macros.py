from ptt_macro import PttMacro
from ptt_menu import SearchBoard, QuickSwitch
from ptt_terminal import PttBoardList
from ptt_board import PttBoard
from ptt_thread import PttThread, ThreadOption

OnBoardingScreen = None
FromLastState = []

pmore_config = [
    # searching a hot-board in hot-board list will only move cursor but not jump to the board?
    PttMacro( [PttBoardList, PttBoard],   b's', [SearchBoard] ),
    # no onboarding screen if jumps from searching in a board?
    PttMacro( FromLastState, b'pttnewhand\r', [PttBoardList, OnBoardingScreen, PttBoard] ),
    # if in onboarding screen, skips
    PttMacro( FromLastState, {PttBoardList: b'\r', OnBoardingScreen: b'\x1b[A'}, [PttBoard] ),
    # if enters the board from board list, onboarding screen will be in [PttBoard] state,
    # send an 'Up' to skip the onboarding screen
    PttMacro( FromLastState, b'\x1b[A', [PttBoard] ),
    # enters the thread at cursor or retry after page up if the thread has been deleted
    PttMacro( FromLastState, b'\r', [PttThread, PttBoard], timeout=True, resend=b'\x1b[5~', retry=5 ),
    PttMacro( [PttThread],   b'o',      [ThreadOption] ),    # enters browser configuration
    PttMacro( FromLastState, b'm',      [ThreadOption], row=-5, pattern='\*顯示', retry=3 ),   # 斷行符號: 顯示
    PttMacro( FromLastState, b'l',      [ThreadOption], row=-4, pattern='\*無',   retry=3 ),   # 文章標頭分隔線: 無
    PttMacro( FromLastState, b' ',      [PttThread] ),    # ends config
    PttMacro( FromLastState, b'\x1b[D', [PttBoard] ),     # Left and exits the thread
    PttMacro( FromLastState, b'\x1a',   [QuickSwitch] ),  # Ctrl-Z brings up quick switch menu
    PttMacro( FromLastState, b't',      [PttBoardList] ),    # goes to 熱門看板
    ]

if __name__ == "__main__":
    import asyncio

    from ptt_macro import MacroContext

    ctx = MacroContext(asyncio.Event(), 1.0)

    def sendToServer(data, per_byte):
        print("Send:", data)
        ctx.event.set()

    class Terminal:
        def currentState(self):
            return None

        def verifyState(self, state):
            return True

        def verifyRow(self, row, pattern):
            return True

    async def run_macro(macros):
        terminal = Terminal()
        for m in macros:
            print(m)
            status = await m.run(sendToServer, terminal, ctx)
            if isinstance(status, str):
                print(status)
            if status is not True:
                break

    asyncio.run(run_macro(pmore_config))

