import socket
import threading
import os
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime

HOST = '127.0.0.1'
PORT = 8080
LOG_FILE = 'logs.txt'
KEEP_ALIVE_TIMEOUT = 10 

#write in log file
def write_log(client_address, requested_file, status_code, method="GET"):
    access_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{client_address[0]} - [{access_time}] - {method} - {requested_file} - {status_code}\n"
    with open(LOG_FILE, 'a', encoding='utf-8') as log_file:
        log_file.write(log_entry)


def get_content_type(file_name):
    if file_name.endswith(('.html', '.htm')):
        return 'text/html; charset=UTF-8'
    elif file_name.endswith('.txt'):
        return 'text/plain; charset=UTF-8'
    elif file_name.endswith('.css'):
        return 'text/css'
    elif file_name.endswith(('.jpg', '.jpeg')):
        return 'image/jpeg'
    elif file_name.endswith('.png'):
        return 'image/png'
    elif file_name.endswith('.gif'):
        return 'image/gif'
    else:
        return 'application/octet-stream'


def format_http_date(timestamp):
    return formatdate(timestamp, usegmt=True)


def parse_headers(request_lines):
    headers = {}
    for line in request_lines[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            headers[key.strip().lower()] = value.strip()
    return headers

#send response
def send_response(client_socket, status_line, content_type, body=b'', last_modified=None, connection='close', send_body=True):

    header = (
        f"{status_line}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
    )

    if last_modified:
        header += f"Last-Modified: {last_modified}\r\n"

    header += ( f"Connection: {connection}\r\n")
    
    if connection == 'keep-alive':   
        header += f"Keep-Alive: timeout={KEEP_ALIVE_TIMEOUT}\r\n"

    header += "\r\n"

    response = header.encode('utf-8')
    if send_body:
        response += body

    print("----- HTTP RESPONSE START -----")
    print(header)
    print("------ HTTP RESPONSE END ------")

    client_socket.sendall(response)


def recv_one_request(client_socket):
    data = b''
    while b'\r\n\r\n' not in data:
        chunk = client_socket.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def handle_single_request(client_socket, client_address, request_data):
    request_text = request_data.decode('utf-8', errors='ignore')

    print(f"\n[CONNECTED] {client_address}")
    print("----- HTTP REQUEST START -----")
    print(request_text)
    print("------ HTTP REQUEST END ------")

    request_lines = request_text.splitlines()
    if len(request_lines) == 0:
        return False

    request_line = request_lines[0]
    parts = request_line.split()

    if len(parts) < 3:
        body = b"<html><body><h1>400 Bad Request</h1></body></html>"
        send_response(client_socket, "HTTP/1.1 400 Bad Request", "text/html; charset=UTF-8", body, connection='close')
        write_log(client_address, "UNKNOWN", "400 Bad Request")
        return False

    method, path, version = parts
    headers = parse_headers(request_lines)

    # only accept GET / HEAD + HTTP/1.1
    if method not in ('GET', 'HEAD') or version != 'HTTP/1.1':
        body = b"<html><body><h1>400 Bad Request</h1></body></html>"
        send_response(client_socket, "HTTP/1.1 400 Bad Request", "text/html; charset=UTF-8", body, connection='close')
        write_log(client_address, path, "400 Bad Request")
        return False

    # Connection header
    connection = headers.get('connection', 'keep-alive').lower()
    if connection not in ('keep-alive', 'close'):
        connection = 'close'
    keep_alive = (connection == 'keep-alive')

    # Default file
    if path == '/':
        file_name = 'index.html'
    else:
        file_name = path.lstrip('/')

    # Remove query string
    if '?' in file_name:
        file_name = file_name.split('?', 1)[0]

    # Security check
    if '..' in file_name:
        body = b"<html><body><h1>403 Forbidden</h1></body></html>"
        send_response(client_socket, "HTTP/1.1 403 Forbidden", "text/html; charset=UTF-8", body, connection=connection)
        write_log(client_address, file_name, "403 Forbidden")
        return keep_alive

    # file not exist
    if not os.path.isfile(file_name):
        if os.path.isfile('404.html'):
            with open('404.html', 'rb') as f:
                body = f.read()
        else:
            body = b"<html><body><h1>404 File Not Found</h1></body></html>"
        send_response(client_socket, "HTTP/1.1 404 File Not Found", "text/html; charset=UTF-8", body, connection=connection, send_body=(method == 'GET'))
        write_log(client_address, file_name, "404 File Not Found")
        return keep_alive

    allowed_ext = (
        '.html', '.htm', '.txt', '.css', '.js',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'
    )
    if not file_name.lower().endswith(allowed_ext):
        body = b"<html><body><h1>403 Forbidden</h1></body></html>"
        send_response(client_socket, "HTTP/1.1 403 Forbidden", "text/html; charset=UTF-8", body, connection=connection)
        write_log(client_address, file_name, "403 Forbidden")
        return keep_alive

    # Last-Modified
    mtime = os.path.getmtime(file_name)
    last_modified = format_http_date(mtime)

    # If-Modified-Since
    if_modified_since = headers.get('if-modified-since')
    if if_modified_since:
        try:
            ims_dt = parsedate_to_datetime(if_modified_since)
            file_dt = datetime.fromtimestamp(mtime, timezone.utc)
        
            if int(file_dt.timestamp()) <= int(ims_dt.timestamp()):
                send_response(
                    client_socket,
                    "HTTP/1.1 304 Not Modified",
                    get_content_type(file_name),
                    b'',
                    last_modified=last_modified,
                    connection=connection,
                    send_body=False
                )
                write_log(client_address, file_name, "304 Not Modified")
                return keep_alive
        except:
            pass

    with open(file_name, 'rb') as f:
        body = f.read()

    content_type = get_content_type(file_name)

    send_response(
        client_socket,
        "HTTP/1.1 200 OK",
        content_type,
        body,
        last_modified=last_modified,
        connection=connection,
        send_body=(method == 'GET')
    )
    write_log(client_address, file_name, "200 OK")
    return keep_alive


def handle_client(client_socket, client_address):
    """
    Handle client request in a separate thread
    Supports keep-alive
    """
    client_socket.settimeout(KEEP_ALIVE_TIMEOUT)  

    try:
        while True:
         try:
            request_data = recv_one_request(client_socket)
            if not request_data:
                break

            keep_alive = handle_single_request(client_socket, client_address, request_data)
            
            if not keep_alive:
                break
         
         except socket.timeout:
                print("\nTimeout,closing socket.")
                break

    except Exception as e:
        print(f"[ERROR] {client_address}: {e}")
    finally:
        client_socket.close()

def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(5)

    print(f"Server is running on http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the server.")

    try:
        while True:
            client_socket, client_address = server_socket.accept()
            client_thread = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address)
            )
            client_thread.start()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server_socket.close()


if __name__ == '__main__':
    main()
