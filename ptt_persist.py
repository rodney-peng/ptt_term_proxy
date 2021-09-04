import os
import signal
import socket
import pickle
import shelve

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
            print("failed to connect:", e)
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

    def send(self, obj, _type):
        if not self.socket:
            print("Not connected!")
            return

        data = pickle.dumps(obj)
        size = len(data)

        try:
            self.socket.sendall(_type.to_bytes(4, 'big') + size.to_bytes(4, 'big'))
            self.socket.sendall(data)
        except Exception as e:
            print("failed to send:", e)
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
            except Exception as e:
                print("Failed to create directory for board", board)
                print(e)
                continue
            for aidc, thread in threads.items():
                print(board, aidc, thread)
                thread.saveContent(os.path.join(cls.archive_dir, board, aidc))

    @classmethod
    def server(cls):
        _sock = None
        _shelve = cls.init_shelve()
        _updates = {}

        def sigterm(signum, frame):
            print("Got signal", signum, frame)
            if _sock: _sock.close()

        signal.signal(signal.SIGTERM, sigterm)

        def handle_conn(conn):
            data = conn.recv(8)
            if not data or len(data) != 8: return False

            _type = int.from_bytes(data[:4], byteorder='big')
            size  = int.from_bytes(data[4:], byteorder='big')

            data = conn.recv(size)
            if not data or len(data) != size: return False
            obj = pickle.loads(data)    # call __setstate__()

            if _type == cls.TYPE_THREAD:
                cls.handle_thread(obj, _shelve, _updates)
            return True

        try:
            os.remove(cls.sock_filename)
        except FileNotFoundError:
            pass

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            _sock = s
            s.bind(cls.sock_filename)
            s.listen(1)
            while True:
                try:
                    conn, _ = s.accept()
                    while handle_conn(conn):
                        pass
                    conn.close()
                except KeyboardInterrupt:
                    break
#                except Exception as e:
#                    print(e)
#                    break

        cls.saveUpdates(_updates)
        _shelve.close()     # call __getstate__() for all threads in the shelve
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
    PttPersist.server()

