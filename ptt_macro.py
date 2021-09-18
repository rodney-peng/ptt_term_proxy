from dataclasses import dataclass
from typing import Tuple

from ptt_event import ClientEvent
from ptt_board import QuickSwitch
from ptt_thread import PttThread, ThreadOption

@dataclass
class PttMacro:
    data: bytes
    state: Tuple = None
    timeout: bool = False
    resend: bytes = None
    retry: int = 0
    row: int = 0
    pattern: str = None

macros_pmore_config = [
    PttMacro( b'\x1a', (QuickSwitch,) ),   # Ctrl-Z
    PttMacro( b'b' ),   # will send to the board SYSOP if no board is viewed previously
    PttMacro( b' ', timeout=True ),     # skips the onboarding screen or allows timeout
    # reads the thread at cursor or retry after cursor Up
    PttMacro( b'r', (PttThread,), timeout=True, resend=b'\x1b[A\x1b[A\x1b[A', retry=5 ),
    PttMacro( b'o', (PttThread, ThreadOption) ),    # enters thread browser config
    PttMacro( b'm', (PttThread, ThreadOption), row=-5, pattern='\*顯示', retry=3 ),   # 斷行符號: 顯示
    PttMacro( b'l', (PttThread, ThreadOption), row=-4, pattern='\*無',   retry=3 ),   # 文章標頭分隔線: 無
    PttMacro( b' ', (PttThread,) ),     # ends config
    PttMacro( b'\x1b[D', tuple() ),     # Left and leaves the thread
    PttMacro( b'\x1a', (QuickSwitch,) ),   # Ctrl-Z
    PttMacro( b'c' ),       # goes to 分類看板
    PttMacro( b'\x1b[D' ),   # Left and goes to 主功能表
    ]

if __name__ == "__main__":
    print(macros_pmore_config)

