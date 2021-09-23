from dataclasses import dataclass
from collections import abc
from typing import Union, List, Dict
import asyncio
import traceback

from ptt_menu import PttMenu


@dataclass
class MacroContext:
    event: asyncio.Event
    wait_interval: float
    resend: bytes = None
    retry: int = -1
    last_state: List[PttMenu] = None

@dataclass
class PttMacro:
    in_state: List[PttMenu]
    send: Union[bytes, Dict[PttMenu, bytes]]
    to_state: List[PttMenu]
    timeout: bool = False
    resend: bytes = None
    retry: int = 0
    row: int = 0
    pattern: str = None

    # return value:
    #   False: to break
    #   True:  to continue
    #   str:   to break with message
    #   bytes: priority data to send
    #   None:  to loop normally
    def handle_macro_event(self, terminal, timeouted, ctx):
        if self.to_state is not None:
            if not terminal.verifyState(self.to_state):
                return "expected state " + repr(self.to_state) + " but " + repr(terminal.currentState())

        if self.timeout and self.resend and self.retry:
            if timeouted or ctx.resend:
                if ctx.retry > 0:
                    if not ctx.resend:
                        print("macro retry:", ctx.retry)
                        ctx.retry -= 1
                        return self.resend
                    else:
                        return True
                else:
                    return "Exceed maximum retry!"

        if self.row and self.pattern and self.retry:
            if not terminal.verifyRow(self.row, self.pattern):
                if ctx.retry > 0:
                    print("macro retry:", ctx.retry)
                    ctx.retry -= 1
                    return True
                else:
                    return "Exceed maximum retry!"
            else:
                print("found", self.pattern)

        return None

    async def run(self, sendToServer, terminal, ctx: MacroContext):
        ctx.event.clear()
        if self.in_state is not None:
            if not terminal.verifyState(self.in_state if self.in_state else ctx.last_state):
                return False

        ctx.retry = self.retry
        ctx.resend = None

        done = False
        error = None
        while not done and error is None:
            if ctx.resend:
                sendToServer(ctx.resend, False)
            elif isinstance(self.send, bytes):
                sendToServer(self.send, False)
            else:
                state = terminal.currentState()
                if state in self.send:
                    sendToServer(self.send[state], False)
                elif terminal.verifyState(self.to_state):
                    done = True
                    break
                else:
                    error = "expected state " + repr(self.to_state) + " but " + repr(state)
                    break

            timeout = False
            try:
                await asyncio.wait_for(ctx.event.wait(), ctx.wait_interval)
            except asyncio.TimeoutError:
                if self.timeout:
                    ctx.event.set()     # ignore timeout and proceed
                    timeout = True
                else:
                    error = "macro event timeout!"
                    break
            except asyncio.CancelledError:
                error = "macro event cancelled!"
                break
            except Exception as e:
                traceback.print_exc()

            if ctx.event.is_set():
                ctx.event.clear()
                try:
                    next = self.handle_macro_event(terminal, timeout, ctx)
                except Exception as e:
                    traceback.print_exc()
                    error = repr(e)
                    break
                if next is True:
                    ctx.resend = None
                    continue
                elif next is False:
                    error = "error!"
                    break
                elif isinstance(next, bytes):
                    ctx.resend = next
                    continue
                elif isinstance(next, str):
                    error = next
                    break
            else:
                error = "macro event is not set!"
                break
            done = True

        ctx.last_state = self.to_state
        return True if done else error


