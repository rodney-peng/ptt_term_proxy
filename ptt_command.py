import re

from ptt_event import ClientEvent, ProxyEvent, ClientContext
from ptt_menu import PttMenu


class CommandBox(PttMenu):

    @staticmethod
    def is_entered(lines):
        yield ProxyEvent.true

    # The command box must only use the last line as a command may alter the content on the screen except the last line
    CommandRow = -1
    CommandCol = 60
    CommandPrompt = "Command:"
    CommandMaxLen = CommandCol - len(CommandPrompt)

    def enter(self, y, x, lines):
        yield from super().enter(y, x, lines)

        yield ProxyEvent.cut_stream(0)

        self.input = ""
        self.screenData = yield ProxyEvent.req_screen_data(ClientContext(self.CommandRow, 1, length=self.CommandCol))
        assert self.screenData is not None
        yield ProxyEvent.ok

        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1, self.CommandPrompt, fg="white", bg="black"))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8, " " * self.CommandMaxLen, fg="white", bg="black", bold=True))
        yield ProxyEvent.draw_client(ClientContext(self.CommandRow, 1+8))

    def exit(self):
        yield from super().exit()
        yield ProxyEvent.send_to_client(self.screenData)
        yield ProxyEvent.reset_rendition
        yield ProxyEvent.draw_cursor
        yield ProxyEvent.resume_stream

    Commands = {}
    Patterns = {}

    def post_client_event(self):
        if False: yield

    def client_event(self, event: ClientEvent):
        yield from super().client_event(event)
        if isinstance(event, str) or ClientEvent.isViewable(event):
            if len(self.input) < self.CommandMaxLen:
                if isinstance(event, str):
                    self.input += event
                    yield ProxyEvent.draw_client(ClientContext(content=event))  # echo
                else:
                    self.input += chr(event)
                    yield ProxyEvent.event_to_client(event)  # echo

                yield from self.post_client_event()
        elif event == ClientEvent.Backspace:
            if self.input:
                self.input = self.input[:-1]
                yield ProxyEvent.send_to_client(b'\b \b')
        elif event == ClientEvent.Enter:
            input = self.input.strip()
            if input:
                if input in self.Commands:
                    yield self.Commands[input](input)
                else:
                    for pattern, event in self.Patterns.items():
                        matched = re.match(pattern, input)
                        if matched:
                            yield event(matched)
                            break
            yield from self.exit()


