import sys
import os
import re
import pyte

from mitmproxy import http

from uao import register_uao
register_uao()

# fix for double-byte character positioning and drawing
class MyScreen(pyte.Screen):

    def cursor_position(self, line=None, column=None):
        line = (line or 1) - 1
        column = (column or 1) - 1
        if line or column:
            # double-bytes character should be treated as width 2
            content = self.display[line]
            chridx = 0  # character index
            curpos = 0  # cursor position
            while curpos < column:
                curpos += (1 if (len(content) <= chridx) or (ord(content[chridx]) < 256) else 2)
                chridx += 1
#            if chridx != curpos:
#                print("col", column, "cur", curpos, "idx", chridx, "|%s|" % content)
            super(MyScreen, self).cursor_position(line + 1, chridx + 1)
        else:
            super(MyScreen, self).cursor_position()

    def draw(self, char):
        content = self.display[self.cursor.y]
        if ord(char) < 256:
            # a single-byte character overwrites a double-byte character
            bInsert = False
            if ord(content[self.cursor.x]) >= 256:
#                print("'%c' overwrites '%c'" % (char, content[self.cursor.x]))
                bInsert = True
            super(MyScreen, self).draw(char)
            if bInsert:
                self.insert_characters()
        else:
            # a double-byte character overwrites two characters
            bDelete = False
            bErase = False
            try:
                # self.cursor.x+1 may be out of boundary
                if ord(content[self.cursor.x]) < 256:
#                    print("'%c' overwrites '%c' '%c'" % (char, content[self.cursor.x], content[self.cursor.x+1]))
                    if ord(content[self.cursor.x+1]) < 256:
                        # two single-byte characters
                        bDelete = True
                    else:
                        # a single-byte character followed by a double-byte character
                        bErase = True
            except:
                pass
            if bDelete:
                self.delete_characters()
            elif bErase:
                self.erase_characters(2)
            super(MyScreen, self).draw(char)

# for event debugging
class MyStream(pyte.Stream):
    def __init__(self, to=sys.stdout, only=(), *args, **kwargs):
        super(MyStream, self).__init__(*args, **kwargs)

        def safe_str(chunk):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            elif not isinstance(chunk, str):
                chunk = str(chunk)

            return chunk

        class Bugger(object):
            __before__ = __after__ = lambda *args: None

            def __getattr__(self, event):
                def inner(*args, **flags):
                    to.write(event.upper() + " ")
                    to.write("; ".join(map(safe_str, args)))
                    to.write(" ")
                    to.write(", ".join("{0}: {1}".format(name, safe_str(arg))
                                       for name, arg in flags.items()))
                    to.write(os.linesep)
                return inner

        self.attach(Bugger(), only=only)


def showScreen(screen):
    lines = screen.display
    print("Cursor:", screen.cursor.y, screen.cursor.x, "Lines:", len(lines))
    for n, line in enumerate(lines, 1):
        print(n, "%r" % line)

def showCursor(screen):
    print("Cursor:", screen.cursor.y, screen.cursor.x, "%r" % screen.display[screen.cursor.y])

def locate_from_screen(screen, bBottom = False):
    lines = screen.display

    if bBottom:
        print("bottom: %r" % lines[-1])

        if re.search("請按.+鍵.*繼續", lines[-1]):
            print("等待按鍵")
            return

        if re.search("請選擇", lines[-1]):
            print("等待選擇")
            return

        if re.match("\s*文章選讀", lines[-1]):
            try:
                board = re.search("^\s*【板主:.+看板《(\w+)》\s*$", lines[0]).group(1)
                print("In board: %r" % board)
            except:
                print("Board missing: %r" % lines[0])

            showCursor(screen)
            return

        # note the pattern '\ *?\d+' to match variable percentage digits
        browse = re.match("\s*瀏覽.+\(\ *?(\d+)%\)\s+目前顯示: 第 (\d+)~(\d+) 行", lines[-1])
        if browse:
            percent = int(browse.group(1))
            lineStart = int(browse.group(2))
            lineEnd = int(browse.group(3))
            print("Browse: %d%%" % percent, "Lines:", lineStart, "~", lineEnd)

            if (lineStart == 1):
                try:
                    board = re.match("\s+作者\s+.+看板\s+(\w+)\s*$", lines[0]).group(1)
                    print("Board: %r" % board)
                except:
                    print("Board missing: %r" % lines[0])

                try:
                    title = re.match("\s+標題\s+(\S.+)\s*$", lines[1]).group(1)
                    print("Title: %r" % title)
                except:
                    print("Title missing: %r" % lines[1])
            return

def server_message(screen, stream, content):
    print("\nserver: (%d)" % len(content))

    stream.feed(content.decode("big5uao", 'replace'))

    # dirty trick to identify the last segment with size
    # (FIXME)
    # but sometimes a segment with size 1021 is not the last or the last segment is larger than 1021
    # (FIXME)
    # a double-byte character could be split into two segments
    if len(content) < 1021:
        locate_from_screen(screen, bBottom=True)

vt_keys = ["Home", "Insert", "Delete", "End", "PgUp", "PgDn", "Home", "End"]
xterm_keys = ["Up", "Down", "Right", "Left", "?", "End", "Keypad 5", "Home"]
def client_message(screen, content):
    print("\nclient:", content)

    sESC = '\x1b'
    sCSI = '['
    sNUM = '0'

    state = None
    number = ""
    for b in content:
        if state != sNUM:
            number = ""

        c = chr(b)
        if state == None:
            if c == sESC:
                state = sESC
            elif c == '\r':
                print("Key: Enter")
        elif state == sESC:
            if c == sCSI:
                state = sCSI
            else:
                state = None
        elif state == sCSI:
            if 'A' <= c <= 'H':
                print("xterm key:", xterm_keys[b - ord('A')])
            elif '0' <= c <= '9':
                state = sNUM
                number += c
                continue
            state = None
        elif state == sNUM:
            if '0' <= c <= '9':
                number += c
                continue
            elif c == '~':
                number = int(number)
                if 1 <= number <= len(vt_keys):
                    print("vt key:", vt_keys[number-1])
            state = None


class SniffWebSocket:

    def __init__(self):
        print("SniffWebSocket init")
        self.screen = MyScreen(128, 32)
#        self.stream = MyStream(only=["draw", "cursor_position"])
        self.stream = pyte.Stream()
        self.stream.attach(self.screen)
        self.lastServerMsgTime = -1

    def on_SIGUSR1(self):
        print("sniffer got SIGUSR1")
        showScreen(self.screen)

    # Websocket lifecycle

    def websocket_handshake(self, flow: http.HTTPFlow):
        """

            Called when a client wants to establish a WebSocket connection. The

            WebSocket-specific headers can be manipulated to alter the

            handshake. The flow object is guaranteed to have a non-None request

            attribute.

        """
        print("websocket_handshake")

    def websocket_start(self, flow: http.HTTPFlow):
        """

            A websocket connection has commenced.

        """
        print("websocket_start")
        self.screen.reset()
        self.stream.reset()
        self.lastServerMsgTime = -1

    def websocket_message(self, flow: http.HTTPFlow):
        """

            Called when a WebSocket message is received from the client or

            server. The most recent message will be flow.messages[-1]. The

            message is user-modifiable. Currently there are two types of

            messages, corresponding to the BINARY and TEXT frame types.

        """

        if flow.websocket:

            flow_msg = flow.websocket.messages[-1]

            '''
            if self.lastServerMsgTime < 0:
                self.lastServerMsgTime = flow_msg.timestamp

            deltaTime = flow_msg.timestamp - self.lastServerMsgTime
            print("\nMessages: %i (%f @%f)" % (len(flow.websocket.messages), flow_msg.timestamp, deltaTime))
            self.lastServerMsgTime = flow_msg.timestamp

            dir = "S>" if flow_msg.from_client else "R<"
            type = "T" if flow_msg.is_text else "B"
            l = len(flow_msg.text) if flow_msg.is_text else len(flow_msg.content)

            print(dir + "[" + type + "] len %i" % l)
            '''

            if flow_msg.from_client:
                client_message(self.screen, flow_msg.content)
            else:
                server_message(self.screen, self.stream, flow_msg.content)


    def websocket_error(self, flow: http.HTTPFlow):
        """

            A websocket connection has had an error.

        """
        print("websocket_error, %r" % flow)

    def websocket_end(self, flow: http.HTTPFlow):
        """

            A websocket connection has ended.

        """
        print("websocket_end")

if __name__ == "__main__":
    import argparse
    import asyncio
    import signal
    import typing
    from time import sleep

    from mitmproxy import options, optmanager, exceptions
    from mitmproxy.tools.main import process_options, run
    from mitmproxy.tools.dump import DumpMaster
    from mitmproxy.tools import cmdline
    from mitmproxy.utils import debug, arg_check
#    from mitmproxy.addons.proxyserver import Proxyserver
#    from mitmproxy.proxy.server import TimeoutWatchdog

    class myDumpMaster(DumpMaster):

        def __init__(
            self,
            options: options.Options,
            with_termlog=True,
            with_dumper=True,
        ) -> None:
            super().__init__(options, with_termlog, with_dumper)
            self._watchdog_time = -1

        def on_SIGUSR1(self):
            print("master got SIGUSR1", self._watchdog_time)
            if self.sniffer:
                self.sniffer.on_SIGUSR1()

        def add_sniffer(self, sniffer):
            self.sniffer = sniffer
            self.addons.add(sniffer)

        async def watch_server(self):
            server = self.addons.get("proxyserver")
            print(server)

            while True:
                await asyncio.sleep(conn.timeout_watchdog.CONNECTION_TIMEOUT // 2)

                for conn in server._connections.values():
                    self._watchdog_time = conn.timeout_watchdog.last_activity
                    # kick watchdog by calling disarm()
                    with conn.timeout_watchdog.disarm():
                        pass


    print("PID", os.getpid())

    debug.register_info_dumpers()

    opts = options.Options(listen_host="127.0.0.1", listen_port=8888)
    master = myDumpMaster(opts)

    master.add_sniffer(SniffWebSocket())
    asyncio.ensure_future(master.watch_server())

    parser = cmdline.mitmdump(opts)

    try:
        args = parser.parse_args()  # filter_args?
    except SystemExit:
        arg_check.check()
        sys.exit(1)

    try:
        opts.set(*args.setoptions, defer=True)
        optmanager.load_paths(
            opts,
            os.path.join(opts.confdir, "config.yaml"),
            os.path.join(opts.confdir, "config.yml"),
        )
        process_options(parser, opts, args)

        if args.options:
            optmanager.dump_defaults(opts, sys.stdout)
            sys.exit(0)
        if args.commands:
            master.commands.dump()
            sys.exit(0)
        '''
        if extra:
            if args.filter_args:
                master.log.info(f"Only processing flows that match \"{' & '.join(args.filter_args)}\"")
            opts.update(**extra(args))
        '''

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, getattr(master, "prompt_for_exit", master.shutdown))
            loop.add_signal_handler(signal.SIGTERM, master.shutdown)
            loop.add_signal_handler(signal.SIGUSR1, master.on_SIGUSR1)
        except NotImplementedError:
            # Not supported on Windows
            pass

        # Make sure that we catch KeyboardInterrupts on Windows.
        # https://stackoverflow.com/a/36925722/934719
        if os.name == "nt":
            async def wakeup():
                while True:
                    await asyncio.sleep(0.2)
            asyncio.ensure_future(wakeup())

        master.run()
    except exceptions.OptionsError as e:
        print("{}: {}".format(sys.argv[0], e), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, RuntimeError):
        pass

#    run(myDumpMaster, cmdline.mitmdump, None)
else:
    addons = [
        SniffWebSocket()
    ]

