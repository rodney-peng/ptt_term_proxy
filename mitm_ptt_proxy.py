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
from dataclasses import dataclass

from mitmproxy import options, optmanager, exceptions
from mitmproxy.tools.main import process_options, run
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.tools import cmdline
from mitmproxy.utils import debug, arg_check
from mitmproxy.proxy.server import TimeoutWatchdog
from mitmproxy.hooks import Hook

# handler = on_signal()
@dataclass
class OnSignalHook(Hook):
    signum: int

ptt_proxy = "ptt_proxy.py"

class myDumpMaster(DumpMaster):

    def __init__(
        self,
        options: options.Options,
        with_termlog=True,
        with_dumper=True,
    ) -> None:
        super().__init__(options, with_termlog, with_dumper)
        self._watchdog_time = -1

    def shutdown(self):
        print("master shutdown!")
        if hasattr(self, "conn_task") and not self.conn_task.done():
            self.conn_task.cancel()
        super().shutdown()

    def SIGUSR1(self):
        print("master got SIGUSR1", self._watchdog_time)
        m = self.addons.get(ptt_proxy)
        if m:
            # m is a Python module
            self.addons.invoke_addon(m, OnSignalHook(signal.SIGUSR1))

    def SIGUSR2(self):
        print("master got SIGUSR2", self._watchdog_time)

    async def conn_watcher(self):
        server = self.addons.get("proxyserver")
        print(server)

        while True:
            try:
                await asyncio.sleep(TimeoutWatchdog.CONNECTION_TIMEOUT // 2)
            except asyncio.CancelledError:
                break

            for conn in server._connections.values():
                self._watchdog_time = conn.timeout_watchdog.last_activity
                # kick watchdog by calling disarm()
                with conn.timeout_watchdog.disarm():
                    pass
        print("conn_watcher finished!")

    def start_watcher(self):
        self.conn_task = asyncio.ensure_future(self.conn_watcher())

print("PID", os.getpid())

# the following is copied from mitmproxy.tools.main.run()
# works as calling run(myDumpMaster, cmdline.mitmdump, None)

debug.register_info_dumpers()

opts = options.Options(listen_host="127.0.0.1", listen_port=8888)
master = myDumpMaster(opts)
master.start_watcher()

# option "scripts" is only available once the default addons are loaded
master.options.update(scripts=[ptt_proxy])
#print(master.addons.lookup)

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
        loop.add_signal_handler(signal.SIGUSR1, master.SIGUSR1)
        loop.add_signal_handler(signal.SIGUSR2, master.SIGUSR2)
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

