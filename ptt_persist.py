import sys
import os
import signal
import socket
import pickle
import shelve
import traceback
import asyncio

from ptt_thread import PttThread, PttThreadPersist


class PttPersist:

    TYPE_THREAD = 1

    archive_dir = "ptt"
    sock_filename = os.path.join(os.path.normpath("/"), "tmp", ".ptt_persist")
    shelve_filename = os.path.join(archive_dir, ".ptt_shelve")

    # client methods

    def __init__(self):
        self.socket = None

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.sock_filename)
        except Exception as e:
            print(f"failed to connect to {self.sock_filename}:", e)
            return None
        else:
            self.socket = s
            return s

    def is_connected(self):
        return self.socket is not None

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None

    def send(self, _type, obj):
        if not self.socket:
            print("Not connected!")
            return

        data = pickle.dumps(obj)
        size = len(data)

        try:
            self.socket.sendall(_type.to_bytes(1, 'big') + size.to_bytes(4, 'big'))
            self.socket.sendall(data)
        except Exception:
            traceback.print_exc()
            self.close()

    # server methods

    @classmethod
    def init_shelve(cls):
        _shelve = shelve.open(cls.shelve_filename, writeback=True)

        if '_metadata' not in _shelve:
            _shelve['_metadata'] = {'elapsed_time': 0, 'total_threads': 0}

        return _shelve

    @classmethod
    def show_shelve(cls):
        _shelve = shelve.open(cls.shelve_filename, writeback=True)
        for category, items in _shelve.items():
            for key, item in items.items():
                print(category, key, item)
                if isinstance(item, PttThread): item.show(False)
        _shelve.cache = {}  # clear cache so that no data is updated
        _shelve.close()

    @classmethod
    def handle_thread(cls, thread, _shelve, _updates):
        aids = thread.aids()
        if aids is None: return

        board = aids[1]
        aidc = aids[3]
        if board in _shelve:
            # the first aidc lookup in the board will call __setstate__() for all threads inside
            if aidc in _shelve[board]:
                if board not in _updates or aidc not in _updates[board]:
                    _shelve[board][aidc].loadContent(os.path.join(cls.archive_dir, board, aidc))

                _shelve[board][aidc].merge(thread)
            else:
                threadp = PttThreadPersist()
                threadp.merge(thread)
                _shelve[board][aidc] = threadp
                _shelve['_metadata']['total_threads'] += 1
        else:
            threadp = PttThreadPersist()
            threadp.merge(thread)
            _shelve[board] = { aidc: threadp }
            _shelve['_metadata']['total_threads'] += 1

        _shelve['_metadata']['elapsed_time'] += thread.elapsedTime

        if board in _updates:
            _updates[board][aidc] = _shelve[board][aidc]
        else:
            _updates[board] = { aidc: _shelve[board][aidc] }

        _shelve[board][aidc].show(False)
        print(_shelve['_metadata'])

    @classmethod
    def saveUpdates(cls, _updates):
        for board, threads in _updates.items():
            try:
                os.makedirs(os.path.join(cls.archive_dir, board), mode=0o775, exist_ok=True)
            except Exception:
                traceback.print_exc()
                continue
            for aidc, thread in threads.items():
                print(board, aidc, thread)
                thread.saveContent(os.path.join(cls.archive_dir, board, aidc))

    cmd_formats = {'.':  "cls.{data}",
                   '?':  "print(cls.{data})",
                   '!':  "{data}",
                   '\\': "print({data})" }

    '''
        Tips for debugging:
        1. first run "dir()" or "vars()" to see what is available, either "self" or "cls" is available most likely
        2. then run "vars(self)" or "vars(cls)" to see what attributes are available
        3. enter the leading character to repeat the last command
    '''
    @classmethod
    async def client_task(cls, reader, writer):

        class _file():
            @staticmethod
            def write(data: str):
                writer.write(data.encode())

            @staticmethod
            def flush():
                pass

        _out = sys.stdout
        _err = sys.stderr

        last_cmds = {'.': None, '?': None, '!': None, '\\': None}
        while True:
            leading = await reader.read(1)
            if not leading: break

            _type = leading[0]

            if _type == cls.TYPE_THREAD:
                data = await reader.read(4)
                if not data or len(data) != 4: break

                size = int.from_bytes(data, byteorder='big')

                data = await reader.read(size)
                if not data or len(data) != size: break

                obj = pickle.loads(data)    # call __setstate__()
                cls.handle_thread(obj, cls.shelve, cls.updates)
            elif _type != ord('\n'):
                data = await reader.readline()
                if not data: break

                data = (leading + data).decode().rstrip('\n').strip()
                if not data: continue
                print("\ncommand:", data)

                if data[0] not in cls.cmd_formats:
                    data = '\\' + data
                if len(data) > 1:
                    cmd = cls.cmd_formats[data[0]].format(data=data[1:])
                    last_cmds[data[0]] = cmd
                else:
                    cmd = last_cmds[data[0]]

                if cmd:
                    print("exec:", cmd)
                    sys.stdout = _file
                    sys.stderr = _file
                    try:
                        exec(cmd)
                    except Exception:
                        traceback.print_exc()
                    finally:
                        sys.stdout = _out
                        sys.stderr = _err
                    await writer.drain()

        writer.close()

    @classmethod
    async def server(cls):
        cls.shelve = cls.init_shelve()
        cls.updates = {}

        def sigterm(signum, frame):
            print("Got signal", signum, frame)
            raise asyncio.CancelledError

        signal.signal(signal.SIGTERM, sigterm)

        try:
            server = await asyncio.start_unix_server(cls.client_task, cls.sock_filename)
            try:
                await server.serve_forever()
            except asyncio.CancelledError:
                print("asyncio.CancelledError in server")
            except Exception:
                traceback.print_exc()
            finally:
                server.close()
        except asyncio.CancelledError:
            pass
        except Exception:
            traceback.print_exc()

        cls.saveUpdates(cls.updates)
        cls.shelve.close()     # call __getstate__() for all threads in the shelve
        print("Server ends!")

    @classmethod
    def getBoards(cls):
        try:
            names = [e.name for e in os.scandir(cls.archive_dir) if e.is_dir()]
        except FileNotFoundError:
            names = []
        return cls.archive_dir, names

    @classmethod
    def getThreads(cls, board):
        root = os.path.join(cls.archive_dir, board)
        try:
            names = [e.name for e in os.scandir(root) if e.is_file()]
        except FileNotFoundError:
            names = []
        return root, names


if __name__ == "__main__":
    try:
        asyncio.run(PttPersist.server())
    except KeyboardInterrupt:
        print("KeyboardInterrupt in main")
    except asyncio.CancelledError:
        print("asyncio.CancelledError in main")

