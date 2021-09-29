import sys
import os
import time

'''
    A customized mitmdump that has preconfigured options and can capture signals and watch connections.
    Connection has a watchdog expires in 10 minutes (refer to CONNECTION_TIMEOUT in mitmproxy/proxy/server.py).
    conn_watcher() will refresh the watchdog timer for each connection.
'''

if __name__ != "__main__":
    sys.exit(1)

import argparse
import asyncio
import signal
import typing
import traceback
from dataclasses import dataclass
import importlib

from mitmproxy import options, optmanager, exceptions
from mitmproxy.tools.main import process_options
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.tools import cmdline
from mitmproxy.utils import debug, arg_check
from mitmproxy.addons.script import load_script
from mitmproxy.hooks import Hook
from mitmproxy.proxy.layers.websocket import WebSocketMessageInjected, WebsocketLayer
from mitmproxy.proxy import events, layer
from wsproto.frame_protocol import Opcode

import ptt_proxy

# handler = on_signal()
@dataclass
class OnSignalHook(Hook):
    signum: int

# handler = on_debug_command()
@dataclass
class OnDebugCommandHook(Hook):
    command: str
    lookup: callable


ptt_proxy_script = "ptt_proxy.py"

class myDumpMaster(DumpMaster):

    def __init__(
        self,
        options: options.Options,
        with_termlog=True,
        with_dumper=True,
    ) -> None:
        super().__init__(options, with_termlog, with_dumper)

        self.proxyserver = self.addons.get("proxyserver")
        assert self.proxyserver is not None

        # wthout script watcher
        #self.ptt_proxy = load_script(os.path.expanduser(ptt_proxy_script))
        self.ptt_proxy = ptt_proxy
        self.addons.add(self.ptt_proxy)

        # with script watcher, option "scripts" is only available once the default addons are loaded
        #self.options.update(scripts=[ptt_proxy_script])
        #print(self.addons.lookup)

        # self.ptt_proxy is a Python module, the same value returned from load_script()
        #self.ptt_proxy = self.addons.get(ptt_proxy_script)
        #assert self.ptt_proxy is not None

        self.options.update(onboarding=False)

        #self.commands.add("self-inject.websocket", self.inject_websocket)

        self.sock_task = asyncio.ensure_future(self.sock_server_task())

        self._watchdog_time = 0
        self.conn_task = asyncio.ensure_future(self.conn_watcher())

    def shutdown(self):
        print("master shutdown!")
        if hasattr(self, "conn_task") and not self.conn_task.done():
            self.conn_task.cancel()
        if hasattr(self, "sock_server"):
            self.sock_server.close()
        if hasattr(self, "sock_task") and not self.sock_task.done():
            self.sock_task.cancel()
        super().shutdown()

    def SIGUSR1(self):
        print("master got SIGUSR1", self._watchdog_time)
        self.addons.invoke_addon(self.ptt_proxy, OnSignalHook(signal.SIGUSR1))

    def SIGUSR2(self):
        print("master got SIGUSR2", self._watchdog_time)

    from mitmproxy.flow import Flow

    #@command.command("self-inject.websocket")  # for addon only
    def inject_websocket(self, flow: Flow, to_client: bool, message: bytes, is_text: bool = True):
        from mitmproxy import http, websocket

        if not isinstance(flow, http.HTTPFlow) or not flow.websocket:
            self.log.warn("Cannot inject WebSocket messages into non-WebSocket flows.")

#        print("self-injected, to_client:", to_client, len(message))
        msg = websocket.WebSocketMessage(
            Opcode.TEXT if is_text else Opcode.BINARY,
            not to_client,
            message
        )
        msg._self_injected = True   # mark a self-injected message
        event = WebSocketMessageInjected(flow, msg)
        try:
            self.proxyserver.inject_event(event)
        except ValueError as e:
            self.log.warn(str(e))

    def sendToServer(self, flow, data):
        assert isinstance(data, bytes)
        to_client = False
        is_text = False
        self.inject_websocket(flow, to_client, data, is_text)
        #self.proxyserver.inject_websocket(flow, to_client, data, is_text)
        #self.commands.call("inject.websocket", flow, to_client, data, is_text)

    def sendToClient(self, flow, data):
        assert isinstance(data, bytes)
        to_client = True
        is_text = False
        self.inject_websocket(flow, to_client, data, is_text)
        #self.commands.call("self-inject.websocket", flow, to_client, data, is_text)

    @classmethod
    def is_self_injected(cls, flow_msg):
        return getattr(flow_msg, "_self_injected", False)

    # only applies to a WebsocketLayer in start state
    def websocketLayerStarted(self, wslayer: WebsocketLayer):
        '''
        # unlikely to get in start state
        if wslayer is None:
            try:
                conn = list(self.proxyserver._connections.values())[0]
                s = list(conn.layer.context.layers[-1].streams.values())[0]
                s1 = list(s.context.layers[-1].streams.values())[0]
                wslayer = s1.context.layers[-1]
            except Exception:
                traceback.print_exc()
        '''
        if isinstance(wslayer, WebsocketLayer) and wslayer is not getattr(self, "wslayer", None):
            print(wslayer._handle_event)
            if wslayer._handle_event in [wslayer.__class__.start, wslayer.start]:
                self.wslayer = wslayer
                self.wsl_relay_messages = wslayer.relay_messages
                wslayer.relay_messages = self.relay_websocket_messages
                print("hijacked!", self.wslayer)
                return True
        return False

    def websocketLayerEnded(self, wslayer: WebsocketLayer):
        pass

    from mitmproxy.proxy.utils import expect

    @expect(events.DataReceived, events.ConnectionClosed, WebSocketMessageInjected)
    def relay_websocket_messages(self, event: events.Event) -> layer.CommandGenerator[None]:
        from mitmproxy.proxy.layers.websocket import WebsocketMessageHook, Fragmentizer

        try:
            if isinstance(event, WebSocketMessageInjected) and self.is_self_injected(event.message):
                message = event.message

                self.wslayer.flow.websocket.messages.append(message)
                yield WebsocketMessageHook(self.wslayer.flow)

                if message.dropped:
                    target = "server" if message.from_client else "client"
                    print("\nself-injected dropped: to", target, message)
                    return

                if message.from_client:
                    dst_ws = self.wslayer.server_ws
                else:
                    dst_ws = self.wslayer.client_ws

                fragmentizer = Fragmentizer([], message.type == Opcode.TEXT)
                for msg in fragmentizer(message.content):
                    yield dst_ws.send2(msg)
                return
            '''
            if isinstance(event, events.DataReceived):
                target = type(event.connection).__name__.lower()
                print(f"\nws data from {target}:", len(event.data))
            elif isinstance(event, WebSocketMessageInjected):
                target = "server" if event.message.from_client else "client"
                print(f"\nws data to {target}:", len(event.message.content))
            else:
                print("\nws event:", event)
            '''
        except Exception:
            traceback.print_exc()
            return

        yield from self.wsl_relay_messages(event)

    def reload_ptt_proxy(self):
        def delete_module(name):
            try:
                del sys.modules[name]
                print("removed module:", name)
            except Exception:
                pass

        delete_module('ptt_terminal')
        delete_module('ptt_boardlist')
        delete_module('ptt_board')
        delete_module('ptt_thread')
        delete_module('ptt_menu')
        delete_module('ptt_event')
        delete_module('ptt_macro')
        delete_module('ptt_macros')

        import glob
        for f in glob.glob("__pycache__/*"):
            try:
                os.remove(f)
                print("removed file:", f)
            except Exception:
                pass

        from importlib import reload

        self.addons.remove(self.ptt_proxy)

        oldproxy = self.ptt_proxy.addons[0]

        self.ptt_proxy = reload(ptt_proxy)

        self.addons.add(self.ptt_proxy)     # invoke LoadHook
        self.ptt_proxy.reload(oldproxy)

    async def conn_watcher(self):
        from mitmproxy.proxy.server import TimeoutWatchdog
        print("conn_watcher starteded!", self.proxyserver)
        while True:
            try:
                await asyncio.sleep(TimeoutWatchdog.CONNECTION_TIMEOUT // 2)
            except asyncio.CancelledError:
                break

            least_recent = float('inf')
            for conn in self.proxyserver._connections.values():
                # kick watchdog by calling disarm()
                with conn.timeout_watchdog.disarm():
                    least_recent = min(least_recent, conn.timeout_watchdog.last_activity)
            if least_recent != float('inf'):
                self._watchdog_time = int(least_recent)

        print("conn_watcher finished!")

    '''
        Tips for debugging:
        1. first run "dir()" or "vars()" to see what is available, either "self" or "cls" is available most likely
        2. then run "vars(self)" or "vars(cls)" to see what attributes are available
        3. enter the leading character to repeat the last command: '.', '?', '!', backslash
           just run "self.cmd_formats" to see the templates
        4. usually command is executed by addon's on_debug_command() method, prefixes the command with ':' to execute in the master
        5. hot-reload an addon, e.g. ptt_proxy.py:

            before run the following commands, go to "主功能表" in the terminal window

            delete the module files from __pycache__
            delete related modules from sys.modules:
                ":!del sys.modules['ptt_term']"
                ":!del sys.modules['ptt_thread']"
            run the method reload_ptt_proxy():
                ":.reload_ptt_proxy()"

            Please don't delete 'ptt_proxy' from sys.modules. Doing so causes error.
    '''
    cmd_formats = {'.':  "self.{data}",
                   '?':  "print(self.{data})",
                   '!':  "{data}",
                   '\\': "print({data})" }

    async def sock_client_task(self, reader, writer):
        def lookup(formats, last_cmds, command):
            if command[0] not in formats:
                prefix = '\\'
                cmd = command
            else:
                prefix = command[0]
                cmd = command[1:]
            if cmd:
                cmd = formats[prefix].format(data=cmd)
                last_cmds[prefix] = cmd
            else:
                cmd = last_cmds[prefix]

            print(f"command: '{command}' -> '{cmd}'")
            return cmd

        from contextlib import redirect_stdout, redirect_stderr

        class _file():
            @staticmethod
            def write(data: str):
                writer.write(data.encode())

            @staticmethod
            def flush():
                pass

        last_cmds = {'.': None, '?': None, '!': None, '\\': None}
        while True:
            if self.sock_task.done(): break
            writer.write("> ".encode())
            await writer.drain()

            data = await reader.readline()
            if not data: break

            data = data.decode().rstrip('\n').strip()
            if not data: continue

            mycmd = (data[0] == ':')
            if mycmd:
                data = data[1:]
                if not data: continue
                with redirect_stdout(_file), redirect_stderr(_file):
                    cmd = lookup(self.cmd_formats, last_cmds, data)
                if not cmd: continue

            with redirect_stdout(_file), redirect_stderr(_file):
                try:
                    if mycmd:
                        exec(cmd)
                    else:
                        self.addons.invoke_addon(self.ptt_proxy, OnDebugCommandHook(data, lookup))
                except Exception:
                    traceback.print_exc()

        writer.close()
        print("sock_client_task finished")

    async def sock_server_task(self):
        print("sock_server_task started!")

        sock_filename = os.path.join(os.path.normpath("/"), "tmp", ".ptt_proxy")
        try:
            self.sock_server = await asyncio.start_unix_server(self.sock_client_task, sock_filename)
            await self.sock_server.serve_forever()
        except asyncio.CancelledError:
            print("sock_server_task cancelled!")
        except Exception:
            traceback.print_exc()

        print("sock_server_task finished!")

print("PID", os.getpid())

# the following is copied from mitmproxy.tools.main.run()
# works as calling run(myDumpMaster, cmdline.mitmdump, None)

# register signal handler for SIGUSR1 and SIGUSR2
#debug.register_info_dumpers()

opts = options.Options(listen_host="127.0.0.1", listen_port=8888)
master = myDumpMaster(opts)

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
#        loop.add_signal_handler(signal.SIGUSR1, master.SIGUSR1)
#        loop.add_signal_handler(signal.SIGUSR2, master.SIGUSR2)
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
except KeyboardInterrupt:
    print("KeyboardInterrupt in main!")

