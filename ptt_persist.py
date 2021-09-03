import os
import socket
import pickle

from ptt_thread import PttThread, PttThreadPersist


class PttPersist:

    TYPE_THREAD = 1

    archive_dir = "ptt"
    sock_filename = os.path.join(os.path.normpath("/"), "tmp", ".ptt_persist")

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
        print("type:", _type, "size:", size)

        try:
            self.socket.sendall(_type.to_bytes(4, 'big') + size.to_bytes(4, 'big'))
            self.socket.sendall(data)
        except Exception as e:
            print("failed to send:", e)
            self.close()

    @classmethod
    def server(cls):
        def handle_conn(conn):
            data = conn.recv(8)
            if not data or len(data) != 8: return False
            _type = int.from_bytes(data[:4], byteorder='big')
            size  = int.from_bytes(data[4:], byteorder='big')
            print("type:", _type, "size:", size)
            data = conn.recv(size)
            if not data or len(data) != size: return False
            obj = pickle.loads(data)
            if _type == cls.TYPE_THREAD:
                obj.show(False)  # works without importing PttThread
            return True

        try:
            os.remove(cls.sock_filename)
        except FileNotFoundError:
            pass

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.bind(cls.sock_filename)
            s.listen(1)
            while True:
                conn, _ = s.accept()
                while handle_conn(conn):
                    pass
                conn.close()

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

    @classmethod
    def saveThread(cls, newlines, board, filename):
        def write(lines, f):
            '''
            merge new and existing content:
            if new line is not empty(has LINE_HOLDER only), always overwrites existing one
            otherwise skip to the next line
            '''
            existing = len(lines)
            for n, text in enumerate(newlines):
                if text != PttThread.LINE_HOLDER:
                    f.write(text + '\n')
                elif n < existing:
                    f.write(lines[n])
                else:
                    f.write('\n')

            n += 1
            print("Write new lines:", n)
            while n < existing:
                f.write(lines[n])
                n += 1
            print("Write total lines:", n)

        pathname = os.path.join(cls.archive_dir, board)
        fullname = os.path.join(pathname, filename)
        try:
            with open(fullname, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) and lines[-1][-1] != "\n":
                print("append extra newline at end!")
                lines[-1] += '\n'
        except FileNotFoundError:
            lines = []
        try:
            if len(lines) == 0: os.makedirs(pathname, mode=0o775, exist_ok=True)
            with open(fullname, "w", encoding="utf-8") as f:
                write(lines, f)
                print("Write", fullname, "bytes", f.tell())
        except Exception as e:
            print(f"Failed to save {fullname}:\n", e)


if __name__ == "__main__":
    PttPersist.server()

