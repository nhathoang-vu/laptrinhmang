import socket
import json
import threading
import time
import uuid
import html as html_escaper  # Để escape HTML entities
import sys  # Thêm vào để khởi động lại
import os

import webview  # pip install pywebview
import requests  # pip install requests
import tempfile
import platform

# --- Cấu hình Client ---
SERVER_IP = '26.162.100.45'  # !!! THAY THẾ BẰNG IP SERVER CỦA BẠN !!!
SERVER_SOCKET_PORT = 55555
SERVER_HTTP_PORT = 5000

# --- Thông tin Client (sẽ được server xác nhận/cấp hoặc người dùng nhập) ---
CLIENT_USERNAME = ""
CLIENT_PASSWORD = ""
ASSIGNED_CLIENT_ID = None
CLIENT_GROUP = "Ungrouped"

# --- Biến trạng thái ---
IS_AUTHENTICATED = False  # Theo dõi trạng thái đăng nhập
AUTH_ATTEMPT_IN_PROGRESS = False  # Ngăn chặn nhiều lần nhấn login cùng lúc
AUTH_FAILED_FROM_SERVER = False  # Cờ báo lỗi từ server
AUTH_FAILED_MESSAGE = "Lỗi xác thực không xác định."  # Tin nhắn lỗi từ server

# --- Biến Global ---
window = None  # Đối tượng cửa sổ webview chính
temp_dir = tempfile.mkdtemp(prefix="nebulacast_client_")
client_socket_global = None  # Socket chính của client


# --- Các hàm tiện ích (giữ nguyên hoặc ít thay đổi) ---
def get_server_content_url(relative_url):
    return f"http://{SERVER_IP}:{SERVER_HTTP_PORT}{relative_url}"


def display_html_in_webview(html_content):
    if window:
        try:
            window.load_html(html_content)
        except Exception as e:
            print(f"Lỗi khi tải HTML vào webview: {e}")
    else:
        print("Webview window chưa được khởi tạo.")


def display_text(text_data):
    escaped_text_data = html_escaper.escape(text_data)
    html_content = f"<html><body style='font-size: 24px; padding: 20px; word-wrap: break-word; background-color: #333; color: #fff;'><pre style='white-space: pre-wrap;'>{escaped_text_data}</pre></body></html>"
    display_html_in_webview(html_content)


def display_image(image_url_on_server, filename):
    full_url = get_server_content_url(image_url_on_server)
    html_content = f"<html><body style='margin:0; background-color: #000; display:flex; justify-content:center; align-items:center; height:100vh;'><img src='{full_url}' style='max-width: 100%; max-height: 100%;' alt='{html_escaper.escape(filename)}'/></body></html>"
    display_html_in_webview(html_content)
    print(f"Đang hiển thị ảnh: {full_url}")


def display_video(video_url_on_server, filename):
    full_video_url = get_server_content_url(video_url_on_server)
    html_content = f"<html><body style='margin:0; background-color: #000; display:flex; justify-content:center; align-items:center; height:100vh;'><video controls autoplay style='max-width: 100%; max-height: 100%; outline:none;' src='{full_video_url}'>Trình duyệt không hỗ trợ thẻ video.</video></body></html>"
    display_html_in_webview(html_content)
    print(f"Đang hiển thị video: {full_video_url}")


def clear_display():
    html_content = "<html><body style='background-color: #111;'></body></html>"
    display_html_in_webview(html_content)
    print("Đã xóa hiển thị.")


def handle_server_command(command_dict):
    global ASSIGNED_CLIENT_ID, CLIENT_USERNAME, CLIENT_GROUP, window
    global AUTH_FAILED_FROM_SERVER, AUTH_FAILED_MESSAGE
    command_type = command_dict.get("type")
    payload = command_dict.get("payload", {})
    print(f"Nhận lệnh từ server: {command_type}, payload: {payload}")

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
    elif command_type == "auth_success":
        global IS_AUTHENTICATED
        if payload.get("status") == "authenticated":
            ASSIGNED_CLIENT_ID = payload.get("client_id")
            CLIENT_USERNAME = payload.get("username", CLIENT_USERNAME)
            CLIENT_GROUP = payload.get("group", CLIENT_GROUP)
            IS_AUTHENTICATED = True
            print(f"Xác thực thành công. Username: {CLIENT_USERNAME}, ID: {ASSIGNED_CLIENT_ID}, Group: {CLIENT_GROUP}")
        else:
            print(f"Xác thực được ghi nhận nhưng trạng thái không rõ: {payload}")
            AUTH_FAILED_FROM_SERVER = True
            AUTH_FAILED_MESSAGE = f"Trạng thái xác thực không mong muốn: {payload.get('status', 'N/A')}"
    elif command_type == "auth_failure":
        AUTH_FAILED_FROM_SERVER = True
        AUTH_FAILED_MESSAGE = payload.get('message', 'Lý do không xác định từ server.')
        print(f"!!! XÁC THỰC THẤT BẠI (server phản hồi): {AUTH_FAILED_MESSAGE} !!!")
    elif command_type == "server_ack":
        print(f"Server xác nhận: {payload}")
    elif command_type == "error":
        print(f"Tin nhắn lỗi từ server: {payload.get('message')}")
    else:
        print(f"Loại lệnh không xác định: {command_type}")


def socket_listener(sock):
    persistent_buffer = ""
    try:
        while True:
            chunk = None
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("Socket listener: Server đã đóng kết nối hoặc tin nhắn trống.")
                    break
                persistent_buffer += chunk.decode('utf-8')
            except UnicodeDecodeError as e:
                problematic_original_data = chunk.hex() if chunk else "N/A"
                print(f"Socket listener: Lỗi giải mã Unicode: {e}. Dữ liệu thô (hex): {problematic_original_data}.")
                persistent_buffer = ""
                continue
            except ConnectionAbortedError:
                print("Socket listener: Kết nối đã bị hủy bởi server.")
                break
            except ConnectionResetError:
                print("Socket listener: Kết nối đã bị reset bởi server.")
                break
            except socket.error as e:
                print(f"Socket listener: Lỗi socket khi nhận dữ liệu: {e}")
                break

            while True:
                obj_start_idx = persistent_buffer.find('{')
                if obj_start_idx == -1:
                    if persistent_buffer.strip(): persistent_buffer = ""
                    break
                if obj_start_idx > 0:
                    persistent_buffer = persistent_buffer[obj_start_idx:]

                open_braces = 0
                obj_end_idx = -1
                for i, char in enumerate(persistent_buffer):
                    if char == '{':
                        open_braces += 1
                    elif char == '}':
                        if open_braces > 0: open_braces -= 1
                        if open_braces == 0:
                            obj_end_idx = i
                            break

                if obj_end_idx != -1:
                    json_str = persistent_buffer[:obj_end_idx + 1]
                    try:
                        command = json.loads(json_str)
                        handle_server_command(command)
                        persistent_buffer = persistent_buffer[obj_end_idx + 1:]
                        if not persistent_buffer.strip(): break
                    except json.JSONDecodeError as e:
                        print(f"Socket listener: Lỗi giải mã JSON: {e} cho tin nhắn: '{json_str}'")
                        persistent_buffer = persistent_buffer[obj_end_idx + 1:]
                else:
                    if len(persistent_buffer) > 2 * 1024 * 1024:
                        print(
                            f"Socket listener: Buffer quá lớn ({len(persistent_buffer)} bytes) và không có JSON hoàn chỉnh. Xóa buffer.")
                        persistent_buffer = ""
                    break
    except Exception as e:
        print(f"Socket listener: Lỗi nghiêm trọng: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Socket listener: Luồng dừng hoạt động.")
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def change_password_on_server(current_password, new_password):
    if not ASSIGNED_CLIENT_ID or not CLIENT_USERNAME:
        print("Không thể đổi mật khẩu. Client chưa xác thực hoặc thiếu ID/Username.")
        return False
    url = f"http://{SERVER_IP}:{SERVER_HTTP_PORT}/api/client/change_password"
    payload = {
        "username": CLIENT_USERNAME, "client_id": ASSIGNED_CLIENT_ID,
        "current_password": current_password, "new_password": new_password
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "success":
            print(f"Đổi mật khẩu thành công trên server: {result.get('message')}")
            return True
        else:
            print(f"Đổi mật khẩu thất bại: {result.get('message')}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Lỗi gửi yêu cầu đổi mật khẩu: {e}")
        return False
    except json.JSONDecodeError:
        print(f"Lỗi giải mã phản hồi server khi đổi mật khẩu.")
        return False


# --- Lớp API cho Webview ---
class Api:
    def __init__(self):
        self.auth_thread = None

    def handle_login_attempt(self, username, password):
        global CLIENT_USERNAME, CLIENT_PASSWORD, AUTH_ATTEMPT_IN_PROGRESS, window

        if AUTH_ATTEMPT_IN_PROGRESS:
            print("Đang xử lý yêu cầu đăng nhập trước đó.")
            return

        AUTH_ATTEMPT_IN_PROGRESS = True
        if window: window.evaluate_js("showLoginProgress('Đang xử lý...');")

        CLIENT_USERNAME = username
        CLIENT_PASSWORD = password

        if self.auth_thread and self.auth_thread.is_alive():
            print("Luồng xác thực trước đó vẫn đang chạy.")

        self.auth_thread = threading.Thread(target=self._perform_authentication_flow)
        self.auth_thread.daemon = True
        self.auth_thread.start()

    def _perform_authentication_flow(self):
        global client_socket_global, ASSIGNED_CLIENT_ID, IS_AUTHENTICATED, window, CLIENT_USERNAME, CLIENT_GROUP
        global AUTH_FAILED_FROM_SERVER, AUTH_FAILED_MESSAGE, AUTH_ATTEMPT_IN_PROGRESS

        AUTH_FAILED_FROM_SERVER = False
        AUTH_FAILED_MESSAGE = "Lỗi xác thực không xác định."
        IS_AUTHENTICATED = False
        ASSIGNED_CLIENT_ID = None

        if client_socket_global:
            try:
                client_socket_global.close()
            except Exception:
                pass
            client_socket_global = None

        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket_global = client_socket

        try:
            if window: window.evaluate_js("showLoginProgress('Đang kết nối tới server...');")
            print(f"Đang kết nối tới server {SERVER_IP}:{SERVER_SOCKET_PORT}...")
            client_socket.connect((SERVER_IP, SERVER_SOCKET_PORT))

            if window: window.evaluate_js("showLoginProgress('Đang gửi yêu cầu xác thực...');")
            print("Đã kết nối. Đang gửi yêu cầu xác thực...")

            auth_request_msg = {
                "type": "auth_request",
                "payload": {
                    "username": CLIENT_USERNAME, "password": CLIENT_PASSWORD,
                    "client_id_hint": platform.node() or f"unknown_host_{uuid.uuid4().hex[:4]}",
                    "supported_formats": ["jpg", "png", "txt", "mp4", "jpeg"]
                }
            }
            client_socket.sendall(json.dumps(auth_request_msg).encode('utf-8'))

            listener_thread = threading.Thread(target=socket_listener, args=(client_socket,))
            listener_thread.daemon = True
            listener_thread.start()

            auth_timeout = 15
            start_time = time.time()

            while (time.time() - start_time) < auth_timeout:
                if IS_AUTHENTICATED:
                    break
                if AUTH_FAILED_FROM_SERVER:
                    break
                if not listener_thread.is_alive():
                    print("Luồng listener đã dừng ngoài dự kiến trong quá trình xác thực.")
                    AUTH_FAILED_FROM_SERVER = True
                    AUTH_FAILED_MESSAGE = "Mất kết nối hoặc listener gặp sự cố."
                    break
                time.sleep(0.1)

            if IS_AUTHENTICATED and ASSIGNED_CLIENT_ID:
                print(f"Xác thực thành công cho {CLIENT_USERNAME}, ID: {ASSIGNED_CLIENT_ID}")
                menu_html_content = self._generate_menu_html()
                if window:
                    window.resize(1024, 768)
                    window.load_html(menu_html_content)
                    window.set_title(f'NebulaCast Client - {CLIENT_USERNAME} ({ASSIGNED_CLIENT_ID})')
            else:
                login_error_message = AUTH_FAILED_MESSAGE if AUTH_FAILED_FROM_SERVER else "Xác thực thất bại hoặc quá thời gian. Vui lòng kiểm tra thông tin đăng nhập hoặc trạng thái server."
                print(f"Xác thực thất bại. Thông báo: {login_error_message}")
                if window:
                    escaped_error = html_escaper.escape(login_error_message)
                    window.evaluate_js(f"showLoginError('{escaped_error}');")
                if client_socket_global:
                    try:
                        client_socket_global.close()
                    except Exception:
                        pass

        except socket.timeout:
            err_msg = f"Hết thời gian kết nối tới server ({SERVER_IP}:{SERVER_SOCKET_PORT})."
            print(err_msg)
            if window: window.evaluate_js(f"showLoginError('{html_escaper.escape(err_msg)}');")
        except socket.error as e:
            err_msg = f"Lỗi kết nối socket: {e}. Server có đang chạy không?"
            print(err_msg)
            if window: window.evaluate_js(f"showLoginError('{html_escaper.escape(err_msg)}');")
        except Exception as e:
            err_msg = f"Lỗi không mong muốn trong quá trình xác thực: {e}"
            print(err_msg)
            if window: window.evaluate_js(f"showLoginError('{html_escaper.escape(err_msg)}');")
        finally:
            AUTH_ATTEMPT_IN_PROGRESS = False

    def _generate_menu_html(self):
        escaped_username = html_escaper.escape(CLIENT_USERNAME)
        escaped_client_id = html_escaper.escape(ASSIGNED_CLIENT_ID if ASSIGNED_CLIENT_ID else "N/A")
        escaped_client_group = html_escaper.escape(CLIENT_GROUP)
        # REMOVED: Nút "Toàn màn hình" đã được xóa khỏi đây
        return f"""
        <html>
        <head>
            <meta charset="UTF-8"><title>NebulaCast Client</title>
            <style>
                body {{ font-family: sans-serif; background-color: #111; color: white; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; margin: 0; text-align: center; }}
                h1, h3 {{ color: white; }} p {{ color: #ccc; }}
                .controls button {{ padding: 10px 20px; margin: 8px; background-color: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; min-width: 140px; }}
                .controls button:hover {{ background-color: #0056b3; }} .controls button.exit {{ background-color: #dc3545; }} .controls button.exit:hover {{ background-color: #c82333; }}
                #passwordChangeForm {{ display:none; background: #333; padding: 25px; border-radius: 8px; margin-top: 20px; width: 320px; box-shadow: 0 0 15px rgba(0,0,0,0.5); }}
                #passwordChangeForm label {{ display: block; margin-bottom: 5px; text-align: left; color: #f0f0f0;}}
                #passwordChangeForm input[type="password"] {{ width: calc(100% - 22px); padding: 10px; margin-bottom: 15px; border-radius: 4px; border: 1px solid #555; background: #444; color: white; font-size: 14px;}}
                #passwordChangeForm button {{ font-size: 14px; padding: 8px 15px; }}
                #passwordChangeStatus {{ color: yellow; margin-top: 10px; min-height: 20px;}}
            </style>
        </head>
        <body>
            <h1>NebulaCast Client - {escaped_username}</h1>
            <p>ID: {escaped_client_id} | Group: {escaped_client_group}</p>
            <p>Đã xác thực. Đang chờ lệnh từ server...</p>
            <div class='controls' style='margin-top: 20px;'>
                <button onclick="pywebview.api.show_password_change_form()">Đổi mật khẩu</button>
                <button class="exit" onclick="pywebview.api.exit_client()">Thoát Client</button>
            </div>
            <div id="passwordChangeForm">
                <h3>Đổi mật khẩu</h3>
                <label for="currentPass">Mật khẩu hiện tại:</label>
                <input type="password" id="currentPass" autocomplete="current-password">
                <label for="newPass">Mật khẩu mới:</label>
                <input type="password" id="newPass" autocomplete="new-password">
                <label for="confirmNewPass">Xác nhận mật khẩu mới:</label>
                <input type="password" id="confirmNewPass" autocomplete="new-password">
                <button onclick="pywebview.api.submit_password_change()" style="background-color: #28a745;">Gửi</button>
                <button onclick="pywebview.api.hide_password_change_form()" style="background-color: #6c757d;">Hủy</button>
                <div id="passwordChangeStatus"></div>
            </div>
        </body></html>"""

    def show_password_change_form(self):
        if window:
            window.evaluate_js("""
                document.getElementById('passwordChangeForm').style.display = 'block';
                document.getElementById('passwordChangeStatus').innerHTML = '';
                document.getElementById('currentPass').value = '';
                document.getElementById('newPass').value = '';
                document.getElementById('confirmNewPass').value = '';
                document.getElementById('currentPass').focus();
            """)

    def hide_password_change_form(self):
        if window: window.evaluate_js("document.getElementById('passwordChangeForm').style.display = 'none';")

    def submit_password_change(self):
        if not window: return
        window.evaluate_js("""
            let current = document.getElementById('currentPass').value;
            let newP = document.getElementById('newPass').value;
            let confirmP = document.getElementById('confirmNewPass').value;
            if (!current || !newP || !confirmP) {
                document.getElementById('passwordChangeStatus').innerText = 'Vui lòng điền đủ các trường.';
            } else if (newP !== confirmP) {
                document.getElementById('passwordChangeStatus').innerText = 'Mật khẩu mới không khớp.';
            } else {
                document.getElementById('passwordChangeStatus').innerHTML = '<span>Đang xử lý...</span>';
                pywebview.api.process_password_change(current, newP, confirmP);
            }
        """)

    def process_password_change(self, current_pass, new_pass, confirm_pass):
        status_message_id = 'passwordChangeStatus'
        if new_pass != confirm_pass:
            if window: window.evaluate_js(
                f"document.getElementById('{status_message_id}').innerText = 'Mật khẩu mới không khớp (Python check).';")
            return

        print("Đang thử đổi mật khẩu qua API...")
        success = change_password_on_server(current_pass, new_pass)

        if success:
            print("Đổi mật khẩu thành công! Yêu cầu khởi động lại.")
            success_html = """
            <p style='color: #28a745; font-weight: bold;'>Đổi mật khẩu thành công!</p>
            <p>Client cần được khởi động lại để áp dụng thay đổi.</p>
            <button onclick='pywebview.api.restart_client()' style='margin-top: 10px; background-color: #007bff; color: white; border: none; border-radius: 5px; padding: 10px 20px; cursor: pointer; font-size: 16px;'>Khởi động lại ngay</button>
            """
            escaped_html = json.dumps(success_html)
            js_code = f"""
            document.getElementById('passwordChangeForm').style.display = 'none';
            const statusDiv = document.getElementById('{status_message_id}');
            statusDiv.innerHTML = {escaped_html};
            """
            if window:
                window.evaluate_js(js_code)
        else:
            msg = 'Đổi mật khẩu thất bại. Kiểm tra lại mật khẩu hiện tại hoặc log server.'
            print(msg)
            if window:
                window.evaluate_js(
                    f"document.getElementById('{status_message_id}').innerText = '{html_escaper.escape(msg)}';")

    # REMOVED: Hàm toggle_fullscreen đã được xóa

    def restart_client(self):
        """Thực hiện dọn dẹp và khởi động lại toàn bộ ứng dụng."""
        print("API: Yêu cầu khởi động lại client...")
        perform_final_cleanup()
        try:
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as e:
            print(f"Lỗi khi khởi động lại: {e}")
            if window:
                window.evaluate_js("alert('Không thể tự động khởi động lại. Vui lòng đóng và mở lại ứng dụng.');")

    def exit_client(self):
        print("Thoát client qua nút trên webview...")
        global IS_AUTHENTICATED, client_socket_global
        IS_AUTHENTICATED = False
        if client_socket_global:
            try:
                client_socket_global.close()
            except Exception as e:
                print(f"Lỗi khi đóng socket lúc thoát: {e}")
        if window:
            window.destroy()
        perform_final_cleanup()
        os._exit(0)


# --- HTML cho trang đăng nhập ---
LOGIN_PAGE_HTML = """
<html><head><meta charset="UTF-8"><title>NebulaCast Client - Đăng nhập</title>
<style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #20232a; color: #e0e0e0; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; margin: 0; text-align: center; }
    .login-container { background-color: #2c2f36; padding: 30px 40px; border-radius: 10px; box-shadow: 0 5px 25px rgba(0,0,0,0.3); width: 340px; }
    h1 { color: #61dafb; margin-bottom: 25px; font-size: 28px; }
    label { display: block; text-align: left; margin-bottom: 8px; font-size: 14px; color: #a0a0a0; }
    input[type="text"], input[type="password"] { width: calc(100% - 24px); padding: 12px; margin-bottom: 20px; border: 1px solid #444851; border-radius: 6px; background-color: #353941; color: #e0e0e0; font-size: 16px; box-sizing: border-box; }
    input[type="text"]:focus, input[type="password"]:focus { outline: none; border-color: #61dafb; box-shadow: 0 0 0 2px rgba(97, 218, 251, 0.3); }
    button { width: 100%; padding: 12px; background-color: #61dafb; color: #20232a; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: bold; transition: background-color 0.3s ease; }
    button:hover { background-color: #52c7e9; } button:disabled { background-color: #4a5568; cursor: not-allowed; }
    #loginStatus { color: #ff6b6b; margin-top: 15px; min-height: 20px; font-size: 14px; word-wrap: break-word; }
    #loginProgress { color: #61dafb; margin-top: 15px; min-height: 20px; font-size: 14px; display: none; }
</style>
</head><body>
<div class="login-container">
    <h1>Client Đăng Nhập</h1>
    <label for="username">Tên đăng nhập:</label>
    <input type="text" id="username" name="username" autocomplete="username" required>
    <label for="password">Mật khẩu:</label>
    <input type="password" id="password" name="password" autocomplete="current-password" required>
    <button id="loginButton" onclick="submitLogin()">Đăng nhập</button>
    <p id="loginStatus"></p>
    <p id="loginProgress"></p>
</div>
<script>
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const loginButton = document.getElementById('loginButton');
    const statusEl = document.getElementById('loginStatus');
    const progressEl = document.getElementById('loginProgress');

    function submitLogin() {
        const username = usernameInput.value.trim();
        const password = passwordInput.value;

        statusEl.textContent = '';
        if (!username || !password) {
            statusEl.textContent = 'Tên đăng nhập và mật khẩu không được để trống.';
            return;
        }
        showLoginProgress('Đang đăng nhập...');
        pywebview.api.handle_login_attempt(username, password);
    }

    function showLoginError(message) {
        statusEl.textContent = message;
        progressEl.style.display = 'none';
        loginButton.disabled = false;
        passwordInput.value = '';
        usernameInput.focus();
    }

    function showLoginProgress(message) {
        statusEl.textContent = '';
        progressEl.style.display = 'block';
        progressEl.textContent = message;
        loginButton.disabled = true;
    }

    [usernameInput, passwordInput].forEach(input => {
        input.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (input === usernameInput && usernameInput.value.trim()) {
                    passwordInput.focus();
                } else {
                    submitLogin();
                }
            }
        });
    });
    window.onload = () => { usernameInput.focus(); };
</script>
</body></html>"""


def start_client_application():
    global window

    print("--- NebulaCast Client ---")
    if SERVER_IP == 'YOUR_SERVER_IP':
        print("!!! LỖI CẤU HÌNH: Vui lòng cập nhật SERVER_IP trong client.py !!!")
        return
    elif SERVER_IP == '26.162.100.45':
        print("!!! LƯU Ý: Đang sử dụng IP server mặc định. Hãy đảm bảo đây là IP đúng. !!!")

    current_api = Api()

    window = webview.create_window(
        'NebulaCast Client - Đăng nhập',
        html=LOGIN_PAGE_HTML,
        js_api=current_api,
        width=500, height=600,
        resizable=True,  # Giữ lại để có thể dùng nút phóng to của OS
        min_size=(400, 500)
    )
    webview.start(debug=False)

    print("Ứng dụng client đã đóng cửa sổ webview hoặc kết thúc tiến trình.")
    perform_final_cleanup()


def perform_final_cleanup():
    global client_socket_global, temp_dir
    print("Thực hiện dọn dẹp cuối cùng cho client...")
    if client_socket_global:
        try:
            print("Đang đóng socket chính của client.")
            client_socket_global.shutdown(socket.SHUT_RDWR)
            client_socket_global.close()
        except Exception as e:
            print(f"Lỗi khi đóng socket: {e}")
        client_socket_global = None

    try:
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"Thư mục tạm {temp_dir} đã được dọn dẹp.")
    except Exception as e_clean:
        print(f"Lỗi khi dọn dẹp thư mục tạm: {e_clean}")
    print("Hoàn tất dọn dẹp cuối cùng.")


if __name__ == '__main__':
    start_client_application()
