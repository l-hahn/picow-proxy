import _thread
import socket
import network
from select import select
from uasyncio import Event
from time import sleep
#from threading import Thread, Event


class PicoProxy:
### CLS FUNCTIONS #############################################################
    wait_symbols = "-\|/"

    def _get_socket(proxy_type="TCP"):
        return socket.socket(
            socket.AF_INET,
            (
                socket.SOCK_STREAM
                if proxy_type == "TCP"
                else
                socket.SOCK_DGRAM
            )
        )
    def init_tunnle(request, sock_in, sock_out, host, port):
        if request.startswith(b"CONNECT"):
            try:
                addr_info = socket.getaddrinfo(host,port)[0][-1]
                sock_out.connect(addr_info)
                sock_in.sendall(b"HTTP/1.1 200 established\r\n\r\n")
            except Exception as e:
                print("Cannot initiate proxy tunnel:", e)


    def proxy_forward_filter(request):
        #looks ugly, yes; but is able to run on Pico W micro-controller :D
        header = request.split('\n')[0]
        url = header.split()[1]
        port = 80
        protocol = None
        has_port = False
        has_protocol = False

        if url.startswith("http"):
            protocol, host_part = url.split('://')
            has_protocol = True
        else:
            host_part = url

        if ":" in host_part:
            splitter = host_part.split(':')
            host_domain = splitter[0]
            port = int(splitter[1])
            has_port = True
        elif "/" in host_part:
            host_domain = host_part.split('/')[0]

        if not has_protocol and has_port:
            if port == 443:
                protocol = "https"
            else:
                protocol = "http"
        if not has_port:
            if protocol == "https":
                port = 443
            else:
                port = 80
        return (protocol, host_domain, port)



### OBJ FUNCTIONS #############################################################
    def __init__(self, buf_byte_size=4096, client_timeout=60):
        self._buf_byte_size = buf_byte_size
        self._client_timeout = client_timeout

        self._listener_event = Event()

        self._is_listening = False
        self._incoming = []
        self._outgoing = []
        self._channel_map = {}
        self._channel_init = {}
        self._channel_from_client = []



    def _set_listener(self):
        # Check what kind of socket is needed to
        # bind onto.
        # Take the first possible socket and the
        # required IP info for binding.
        self._addr_listen = socket.getaddrinfo(
            self._addr, self._port
        )[0][-1]

        if hasattr(self, '_socket_listen') and self._socket_listen is not None:
            self.stop()
        self._socket_listen = PicoProxy._get_socket()
        self._socket_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def listen(self, addr, port, proxy_type="TCP", backlog=0):
        if not self._is_listening:
            self._addr = addr
            self._port = port
            self._proxy_type = "UDP" if not proxy_type == "TCP" else proxy_type

            self._set_listener()

            self._socket_listen.bind(self._addr_listen)
            self._socket_listen.listen(backlog)

            self._incoming.append(self._socket_listen)

            #self._listen_thread = Thread(
            #    target=self._listener_thread, args=(self._listener_event,)
            #)
            #self._listen_thread.start()
            #_thread.start_new_thread(
            #    self._listener_thread, (self._listener_event,)
            #)
            print(f"init done for serving on {self._addr_listen}")
            self._is_listening = True
            self._listener_thread(self._listener_event)

    def stop(self):
        if self._is_listening:
            self._listener_event.set()
            ctr = 0
            while not self._listener_event.is_set():
                print(
                    (
                        "Waiting for listener thread to finish... "
                        f"{PicoProxy.wait_symbols[ctr%len(PicoProxy.wait_symbols)]}\r"
                    ),
                    end=""
                )
                ctr += 1
                sleep(0.5)
            else:
                print("Listener thread finished closing safely.")
            self._socket_listen.close()
            self._is_listening = False
            self._incoming.clear()
            self._outgoing.clear()
            self._channel_map.clear()
            self._channel_init.clear()
            self._channel_from_client.clear()

    def is_active(self):
        return not self._listener_event.is_set()


    def _listener_thread(self, event):
        while self._incoming and not event.is_set():
            inrecv, outsend, excpt = select(
                self._incoming, self._outgoing, self._incoming
            )
            for sock in inrecv:
                if sock is self._socket_listen:
                    self._handle_connection_incoming()
                elif (
                    id(sock) in self._channel_init and not self._channel_init[id(sock)] and
                    sock not in self._channel_from_client
                ):
                    continue
                else:
                    data = sock.recv(self._buf_byte_size)
                    if data:
                        self._handle_connection_receive(sock, data)
                    else:
                        self._handle_connection_close(sock)
        event.clear()

    def _handle_connection_incoming(self):
        conn, addr = self._socket_listen.accept()
        conn.settimeout(self._client_timeout)
        reverse_conn = PicoProxy._get_socket(self._proxy_type)
        reverse_conn.settimeout(self._client_timeout)

        self._channel_from_client.append(conn)

        self._incoming.append(conn)

        self._channel_map[id(conn)] = reverse_conn
        self._channel_map[id(reverse_conn)] = conn

        self._channel_init[id(conn)] = False
        self._channel_init[id(reverse_conn)] = False

    def _handle_connection_receive(self, sock, data):
        reverse_sock = self._channel_map[id(sock)]
        if not self._channel_init[id(sock)] and not self._channel_init[id(reverse_sock)]:
            protocol, host_domain, port = PicoProxy.proxy_forward_filter(data.decode())
            if protocol == "https" or port == 443:
                PicoProxy.init_tunnle(
                    data, sock, reverse_sock, host_domain, port
                )
            else:
                addr_info = socket.getaddrinfo(host_domain,port)[0][-1]
                reverse_sock.connect(addr_info)
                #not a tunnel request, directly forward
                reverse_sock.sendall(data)
            self._incoming.append(reverse_sock)
            self._channel_init[id(sock)] = True
            self._channel_init[id(reverse_sock)] = True
        else:
            reverse_sock.sendall(data)

    def _handle_connection_close(self, sock):
        reverse_sock = self._channel_map[id(sock)]
        for s in (sock, reverse_sock):
            if s in self._outgoing:
                self._outgoing.remove(s)
            if s in self._incoming:
                self._incoming.remove(s)
            if s in self._channel_from_client:
                self._channel_from_client.remove(s)
            s.close()
            del self._channel_init[id(s)]
            del self._channel_map[id(s)]

def connect_wlan(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        if wlan.status() < 0 or wlan.status() >= 3:
            break
        max_wait -= 1
        print(
            "waiting for connection...",
            f"{PicoProxy.wait_symbols[max_wait%4]}\r",
            end=""
        )
        sleep(0.5)
    if wlan.status() != 3:
        raise RuntimeError('network connection failed')
    else:
        status = wlan.ifconfig()
        print(f'conntected, ip = {status[0]}')
    return wlan

def main():
    ssid = 'WLAN-NAME'
    password = 'WLAN-PASSWORD'
    wlan = connect_wlan(ssid, password)
    proxy = PicoProxy()
    proxy.listen(addr='0.0.0.0', port=8080)
    # ctr = 0
    # cnt = len(PicoProxy.wait_symbols)
    # while proxy.is_active():
    #     ctr = (ctr + 1)%cnt
    #     print(
    #         "Currently doing proxy stuff...",
    #         f"{PicoProxy.wait_symbols[ctr]}\r",
    #         end=""
    #     )
    #     sleep(0.5)



if __name__ == "__main__":
    main()