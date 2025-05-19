import socket
import json
import threading
import time
import webview  # pip install pywebview
import requests  # pip install requests
import os
import tempfile  # Để lưu file tạm   thờ i
import platform  # Để lấy hostname

# --- Cấu hình Client ---
# !!! THAY THẾ 'YOUR_SERVER_IP' BẰNG ĐỊA CHỈ IP CỦA MÁY CHẠY SERVER TRÊN MẠNG LAN CỦA BẠN !!!
SERVER_IP = '192.168.1.10'  # Ví dụ: '192.168.1.101'
SERVER_SOCKET_PORT = 55555
SERVER_HTTP_PORT = 5000  # Port Flask của server để fetch content

# Cố gắng lấy hostname làm client_id mặc định, nếu không được thì dùng id cố định
try:
    CLIENT_ID = platform.node() or "DefaultClient"
except Exception:
    CLIENT_ID = "DefaultClient"
CLIENT_GROUP = "Ungrouped"  # Nhóm mặc định, server có thể ghi đè hoặc client tự khai báo

# --- Biến Global cho Webview ---
window = None
temp_dir = tempfile.mkdtemp(prefix="nebulacast_client_")  # Thư mục tạm để lưu content


def get_server_content_url(relative_url):
    return f"http://{SERVER_IP}:{SERVER_HTTP_PORT}{relative_url}"


def display_html_in_webview(html_content):
    if window:
        window.load_html(html_content)
    else:
        print("Webview window not initialized.")


def display_text(text_data):
    html = f"""
    <html><body style='font-size: 24px; padding: 20px; word-wrap: break-word; background-color: #333; color: #fff;'>
    <pre style='white-space: pre-wrap;'>{text_data}</pre>
    </body></html>"""
    display_html_in_webview(html)


def display_image(image_url_on_server, filename):
    local_image_path = ""
    
    full_url = get_server_content_url(image_url_on_server)
    print(f"Fetching image from: {full_url}")
    html = f"""
        <html><body style='margin:0; background-color: #000; display:flex; justify-content:center; align-items:center; height:100vh;'>
        <img src='{full_url}' style='max-width: 100%; max-height: 100%;'/>
        </body></html>"""
    display_html_in_webview(html)
    print(f"Displaying image: {full_url}")

def display_video(video_url_on_server, filename):
    # Đối với video, chúng ta có thể trỏ trực tiếp đến URL trên server
    # vì thẻ <video> của HTML5 có thể stream từ HTTP source.
    # Hoặc tải về nếu muốn phát offline hoặc có vấn đề với streaming trực tiếp.
    # Hiện tại, thử streaming trực tiếp.
    full_video_url = get_server_content_url(video_url_on_server)
    print(f"Attempting to stream video from: {full_video_url}")

    html = f"""
    <html><body style='margin:0; background-color: #000; display:flex; justify-content:center; align-items:center; height:100vh;'>
    <video controls autoplay style='max-width: 100%; max-height: 100%; outline:none;' src='{full_video_url}'>
        Your browser does not support the video tag.
    </video>
    </body></html>"""
    display_html_in_webview(html)
    print(f"Displaying video: {full_video_url}")


def clear_display():
    html = "<html><body style='background-color: #111;'></body></html>"  # Màn hình trống/đen
    display_html_in_webview(html)
    print("Display cleared.")


def handle_server_command(command_dict):
    global CLIENT_ID  # Cho phép cập nhật CLIENT_ID từ server_ack
    command_type = command_dict.get("type")
    payload = command_dict.get("payload", {})
    print(f"Received command: {command_type}, payload: {payload}")

    if command_type == "display_content":
        content_type = payload.get("content_type")
        if content_type == "text":
            display_text(payload.get("data", "No text content."))
        elif content_type == "image":
            display_image(payload.get("url"), payload.get("filename", "image.jpg"))
        elif content_type == "video":
            display_video(payload.get("url"), payload.get("filename", "video.mp4"))
    elif command_type == "clear_content":
        clear_display()
    elif command_type == "server_ack":
        if payload.get("status") == "connected":
            new_client_id = payload.get("client_id")
            if new_client_id:
                CLIENT_ID = new_client_id  # Cập nhật client_id nếu server gán mới (tránh trùng)
                print(f"Successfully connected to server. My ID is now: {CLIENT_ID}")
            else:
                print("Connected to server.")
        else:
            print(f"Server acknowledgement: {payload}")
    else:
        print(f"Unknown command type: {command_type}")


def socket_listener(sock):
    try:
        while True:
            message_str = sock.recv(4096).decode('utf-8')  # Tăng buffer size
            if not message_str:
                print("Server closed connection or message empty.")
                break

            # Xử lý trường hợp nhiều JSON object được gửi cùng lúc (ít khi xảy ra với TCP nhưng đề phòng)
            # Đơn giản là tìm dấu {} để tách, không phải cách parse stream JSON hoàn hảo
            # nhưng đủ dùng cho trường hợp này nếu message không quá phức tạp.
            buffer = ""
            open_braces = 0
            for char in message_str:
                buffer += char
                if char == '{':
                    open_braces += 1
                elif char == '}':
                    open_braces -= 1
                    if open_braces == 0 and buffer.strip():
                        try:
                            command = json.loads(buffer)
                            handle_server_command(command)
                        except json.JSONDecodeError as e:
                            print(f"JSON Decode Error: {e} for message part: '{buffer}'")
                        buffer = ""  # Reset buffer
            if buffer.strip():  # Nếu còn sót lại gì đó không phải JSON hoàn chỉnh
                print(f"Warning: Incomplete JSON segment left in buffer: '{buffer}'")


    except ConnectionResetError:
        print("Connection to server was reset.")
    except Exception as e:
        print(f"Error in socket listener: {e}")
    finally:
        print("Socket listener thread stopped.")
        sock.close()
        # Có thể thêm logic reconnect ở đây nếu muốn
        # os._exit(1) # Thoát hẳn chương trình client nếu mất kết nối


def start_client():
    global window
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        print(f"Attempting to connect to server {SERVER_IP}:{SERVER_SOCKET_PORT}...")
        client_socket.connect((SERVER_IP, SERVER_SOCKET_PORT))
        print("Connected to server.")

        # Gửi client_hello
        hello_message = {
            "type": "client_hello",
            "payload": {
                "client_id": CLIENT_ID,
                "supported_formats": ["jpg", "png", "txt", "mp4"],  # Ví dụ
                "group": CLIENT_GROUP
            }
        }
        client_socket.sendall(json.dumps(hello_message).encode('utf-8'))

        # Khởi động thread lắng nghe lệnh từ server
        listener_thread = threading.Thread(target=socket_listener, args=(client_socket,))
        listener_thread.daemon = True
        listener_thread.start()

        # Khởi tạo và chạy webview UI
        initial_html = "<html><body style='background-color: #111;'><h1 style='color:white; text-align:center; margin-top: 40vh;'>Client</h1><p style='color:white; text-align:center;'>Waiting for server commands...</p></body></html>"
        window = webview.create_window('Màn Hình Hiển Thị', html=initial_html, fullscreen=True, on_top=True)
        webview.start(debug=True)  # debug=True để có console của webview

    except socket.error as e:
        print(f"Socket connection error: {e}. Is the server running at {SERVER_IP}:{SERVER_SOCKET_PORT}?")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Client application attempting to clean up...")
        client_socket.close()
        # Dọn dẹp thư mục tạm
        try:
            for item in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, item))
            os.rmdir(temp_dir)
            print(f"Temporary directory {temp_dir} cleaned up.")
        except Exception as e_clean:
            print(f"Error cleaning temp directory: {e_clean}")
        print("Client application finished.")


if __name__ == '__main__':
    if SERVER_IP == 'YOUR_SERVER_IP':
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!! LỖI CẤU HÌNH: Vui lòng cập nhật SERVER_IP trong client_app.py           !!!")
        print("!!! thành địa chỉ IP của máy chủ NebulaCast trên mạng LAN của bạn.         !!!")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    else:
        start_client()
