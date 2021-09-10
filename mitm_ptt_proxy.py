import sys
import os

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

from mitmproxy import options, optmanager, exceptions
from mitmproxy.tools.main import process_options
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.tools import cmdline
from mitmproxy.utils import debug, arg_check
from mitmproxy.addons.script import load_script

from mitmproxy.proxy.layers.websocket import WebSocketMessageInjected, WebsocketLayer
from mitmproxy.proxy import events, layer


ptt_proxy = "ptt_proxy.py"

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
        mod = load_script(os.path.expanduser(ptt_proxy))
        self.addons.add(mod)

        # with script watcher, option "scripts" is only available once the default addons are loaded
        #self.options.update(scripts=[ptt_proxy])
        #print(self.addons.lookup)

        self.options.update(onboarding=False)

        #self.commands.add("self-inject.websocket", self.inject_websocket)

        self._watchdog_time = 0
        self.conn_task = asyncio.ensure_future(self.conn_watcher())

    def shutdown(self):
        print("master shutdown!")
        if hasattr(self, "conn_task") and not self.conn_task.done():
            self.conn_task.cancel()
        super().shutdown()

    def SIGUSR1(self):
        from dataclasses import dataclass
        from mitmproxy.hooks import Hook
        # handler = on_signal()
        @dataclass
        class OnSignalHook(Hook):
            signum: int

        print("master got SIGUSR1", self._watchdog_time)
        m = self.addons.get(ptt_proxy)
        if m:
            # m is a Python module
            self.addons.invoke_addon(m, OnSignalHook(signal.SIGUSR1))

    def SIGUSR2(self):
        print("master got SIGUSR2", self._watchdog_time)

    from mitmproxy.flow import Flow
    # TODO: figure a better way to distinguish a self-injected message
    injected_mark = b'self-injected:'

    #@command.command("self-inject.websocket")  # for addon only
    def inject_websocket(self, flow: Flow, to_client: bool, message: bytes, is_text: bool = True):
        from mitmproxy import http, websocket
        from wsproto.frame_protocol import Opcode

        class webSocketMessage(websocket.WebSocketMessage):

            @classmethod
            def from_state(cls, state: websocket.WebSocketMessageState):
                print("from_state:", state)
                return super().from_state(state)

            def set_state(self, state: websocket.WebSocketMessageState) -> None:
                print("set_state:", state)
                super().set_state(state)


        if not isinstance(flow, http.HTTPFlow) or not flow.websocket:
            self.log.warn("Cannot inject WebSocket messages into non-WebSocket flows.")

        print("self-injected, to_client:", to_client, len(message))
        msg = webSocketMessage(
            Opcode.TEXT if is_text else Opcode.BINARY,
            not to_client,
            self.injected_mark + message,
            123      # mark a self-injected message, but not sustained to websocket_message()
        )
        event = WebSocketMessageInjected(flow, msg)
        try:
            self.proxyserver.inject_event(event)
        except ValueError as e:
            self.log.warn(str(e))

    def sendToServer(self, flow, data):
        assert isinstance(data, bytes)
        print("sendToServer:", data)
        to_client = False
        is_text = False
        self.proxyserver.inject_websocket(flow, to_client, data, is_text)
        #self.commands.call("inject.websocket", flow, to_client, data, is_text)

    def sendToClient(self, flow, data):
        assert isinstance(data, bytes)
        print("sendToClient:", len(data))
        to_client = True
        is_text = False
        self.inject_websocket(flow, to_client, data, is_text)
        #self.commands.call("self-inject.websocket", flow, to_client, data, is_text)

    def is_self_injected(self, flow_msg):
        marklen = len(self.injected_mark)
        if len(flow_msg.content) > marklen and flow_msg.content[:marklen] == self.injected_mark:
            flow_msg.content = flow_msg.content[marklen:]
            print("self-injected, from_client:", flow_msg.from_client, len(flow_msg.content), flow_msg.timestamp)
            return True
        return False

    # cannot be called from within a websocket addon
    def hijackWebsocketEvent(self, wslayer: WebsocketLayer = None):
        if wslayer is None:
            try:
                conn = list(self.proxyserver._connections.values())[0]
                s = list(conn.layer.context.layers[-1].streams.values())[0]
                s1 = list(s.context.layers[-1].streams.values())[0]
                wslayer = s1.context.layers[-1]
            except Exception:
                traceback.print_exc()

        if isinstance(wslayer, WebsocketLayer):
            print(wslayer._handle_event)
            if wslayer._handle_event == wslayer.relay_messages:
                self.ws_layer = wslayer
                self.ws_handle_event = wslayer._handle_event
                self.ws_layer._handle_event = self.relay_messages
                print("hijackWebsocketEvent!")
                return True
        return False

    def restoreWebsocketEvent(self):
        if hasattr(self, "ws_layer") and self.ws_layer._handle_event == self.relay_messages:
            self.ws_layer._handle_event = self.ws_handle_event

    from mitmproxy.proxy.utils import expect

    @expect(events.DataReceived, events.ConnectionClosed, WebSocketMessageInjected)
    def relay_messages(self, event: events.Event) -> layer.CommandGenerator[None]:
        try:
            if isinstance(event, events.DataReceived):
                target = type(event.connection).__name__.lower()
                print("ws data:", len(event.data), target)
            elif isinstance(event, WebSocketMessageInjected):
                print("ws inject:", len(event.message.content), event.message.timestamp)
            else:
                print("ws event:", event)
        except Exception:
            traceback.print_exc()

        yield from self.ws_handle_event(event)

    async def conn_watcher(self):
        from mitmproxy.proxy.server import TimeoutWatchdog
        print("conn_watcher starteded!", self.proxyserver)
        '''
        # Hijack Websocket event once connection is established, Ugly!!!
        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                print("conn_watcher cancelled!")
                return

            if len(self.proxyserver._connections) and self.hijackWebsocketEvent():
                break
        '''
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

