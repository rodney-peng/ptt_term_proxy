import sys
import os

if __name__ != "__main__":
    sys.exit(1)

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
#from mitmproxy.addons.proxyserver import Proxyserver
from mitmproxy.proxy.server import TimeoutWatchdog

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
        if self.sniffers:
            for addon in self.sniffers:
                if hasattr(addon, "on_SIGUSR1"):
                    addon.on_SIGUSR1()

    def on_SIGUSR2(self):
        print("master got SIGUSR2", self._watchdog_time)
        if self.sniffers:
            for addon in self.sniffers:
                if hasattr(addon, "on_SIGUSR2"):
                    addon.on_SIGUSR2()

    def add_sniffer(self, *sniffers):
        self.sniffers = sniffers
        self.addons.add(*sniffers)

    async def conn_watcher(self):
        server = self.addons.get("proxyserver")
        print(server)

        while True:
            try:
                await asyncio.sleep(TimeoutWatchdog.CONNECTION_TIMEOUT // 2)
            except asyncio.CancelledError:
                break

            for conn in server._connections.values():
                # kick watchdog by calling disarm()
                with conn.timeout_watchdog.disarm():
                    pass
                self._watchdog_time = conn.timeout_watchdog.last_activity
        print("conn_watcher finished!")

    async def reload_watcher(self, event):
#        from importlib import reload
        print("Reload watcher started!")
        while True:
            try:
                await event.wait()
            except asyncio.CancelledError:
                break
            else:
                print("clear reload event!")
                event.clear()

            print("Reload addon!")
            if self.sniffers:
                for addon in self.sniffers:
                    self.addons.remove(addon)
                del self.sniffers

#            reload(SniffWebSocket)
#            print(SniffWebSocket)
            self.add_sniffer(*[SniffWebSocket()])

        print("reload_watcher finished!")

    def start_watcher(self):
        asyncio.ensure_future(self.conn_watcher())

# TODO: can reload the sniffer addon on SIGUSR2
#        self.reload_event = asyncio.Event()
#        asyncio.ensure_future(self.reload_watcher(self.reload_event))

print("PID", os.getpid())

# the following is copied from mitmproxy.tools.main.run()
# works as calling run(myDumpMaster, cmdline.mitmdump, None)

debug.register_info_dumpers()

opts = options.Options(listen_host="127.0.0.1", listen_port=8888)
master = myDumpMaster(opts)

from mitm_ptt_addon import addons as ptt_sniffers
master.add_sniffer(*ptt_sniffers)

master.start_watcher()

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
        loop.add_signal_handler(signal.SIGUSR2, master.on_SIGUSR2)
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

