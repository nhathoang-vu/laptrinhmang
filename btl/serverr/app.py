import flask
from flask import Flask, render_template, request, jsonify, send_from_directory
import socket
import threading
import json
import os
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash # For password hashing

# --- Cấu hình Server ---
SERVER_HOST = '26.162.100.45' # Hoặc IP của bạn
FLASK_PORT = 5000
SOCKET_PORT = 55555
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')

# --- Quản lý User (trong bộ nhớ - Cần giải pháp bền vững hơn cho production) ---
# Username là key, value chứa hashed_password và client_id (có thể là username nếu duy nhất hoặc ID khác)
# Trong ví dụ này, client_id sẽ được server gán/xác nhận và có thể khác username.
users_credentials = {
    "user1": {"hashed_password": generate_password_hash("1"), "client_id_hint": "client_pc_1", "current_socket": None, "group": "all"},
    "user2": {"hashed_password": generate_password_hash("2"), "client_id_hint": "client_pc_2", "current_socket": None, "group": "all"},
}
# client_id sẽ là định danh duy nhất và ổn định cho mỗi client sau khi xác thực.
# Username có thể được dùng để đăng nhập, nhưng client_id dùng cho các thao tác nội bộ.

# --- Quản lý Client và Nhóm ---
connected_clients = {}  # {client_socket: {"username": "user1", "client_id": "actual_client_id", "address": addr, "group": "group_name", "current_content": {}, "history": []}}
client_groups = {"all": []}

# --- Khởi tạo Flask App ---
app = Flask(__name__)
app.config['CONTENT_DIR'] = CONTENT_DIR

# --- Flask Routes ---
@app.route('/')
def index():
    image_files = []
    video_files = []
    text_files = []
    try:
        image_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'images')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'images', f))]
    except FileNotFoundError: pass
    try:
        video_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'videos')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'videos', f))]
    except FileNotFoundError: pass
    try:
        text_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'texts')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'texts', f))]
    except FileNotFoundError: pass

    clients_info = []
    for sock, info in connected_clients.items():
        clients_info.append({
            "username": info["username"], # Hiển thị username
            "client_id": info["client_id"], # Dùng client_id cho các thao tác
            "address": f"{info['address'][0]}:{info['address'][1]}",
            "group": info.get("group", "N/A"),
            "current_content": info.get("current_content", {"type": "N/A", "value": "N/A"}),
            "history": info.get("history", [])
        })
    return render_template('index.html',
                           clients=clients_info,
                           groups=list(client_groups.keys()),
                           image_files=image_files,
                           video_files=video_files,
                           text_files=text_files)

@app.route('/content/<path:subpath>')
def serve_content(subpath):
    return send_from_directory(app.config['CONTENT_DIR'], subpath)

@app.route('/api/send_command', methods=['POST'])
def send_command_route():
    data = request.json
    target_type = data.get('target_type')
    target_id = data.get('target_id') # Đây sẽ là client_id (không phải username) hoặc group_name
    content_type = data.get('content_type')
    content_value = data.get('content_value')

    command_payload = {}
    current_content_summary = {"type": "N/A", "value": "N/A", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    if content_type == 'text_direct':
        command_payload = {"type": "display_content", "payload": {"content_type": "text", "data": content_value}}
        current_content_summary = {"type": "Text (Direct)", "value": content_value[:50] + "..." if len(content_value) > 50 else content_value, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    elif content_type == 'text_file':
        filepath = os.path.join('texts', content_value)
        try:
            with open(os.path.join(app.config['CONTENT_DIR'], filepath), 'r', encoding='utf-8') as f: text_data = f.read()
            command_payload = {"type": "display_content", "payload": {"content_type": "text", "data": text_data}}
            current_content_summary = {"type": "Text File", "value": content_value, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        except Exception as e: return jsonify({"status": "error", "message": f"Error reading text file: {str(e)}"}), 500
    elif content_type in ['image', 'video']:
        file_url = f"/content/{content_type}s/{content_value}"
        command_payload = {"type": "display_content", "payload": {"content_type": content_type, "url": file_url, "filename": content_value}}
        current_content_summary = {"type": content_type.capitalize(), "value": content_value, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    elif content_type == 'clear':
        command_payload = {"type": "clear_content", "payload": {}}
        current_content_summary = {"type": "Cleared", "value": "N/A", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    else: return jsonify({"status": "error", "message": "Invalid content type"}), 400

    if not command_payload: return jsonify({"status": "error", "message": "Could not create command payload"}), 500

    send_nccp_command(target_type, target_id, command_payload, current_content_summary)
    return jsonify({"status": "success", "message": "Command sent"})

@app.route('/api/manage_group', methods=['POST'])
def manage_group_route():
    data = request.json
    action = data.get('action')
    group_name = data.get('group_name')
    target_client_id = data.get('client_id') # Nhận client_id

    if action == "create":
        if group_name and group_name not in client_groups:
            client_groups[group_name] = []
            return jsonify({"status": "success", "message": f"Group '{group_name}' created."})
        return jsonify({"status": "error", "message": "Invalid group name or group exists."}), 400
    
    elif action == "assign":
        target_socket = None
        for sock, info in connected_clients.items():
            if info["client_id"] == target_client_id: # So sánh với client_id
                target_socket = sock
                break
        
        if not target_socket: return jsonify({"status": "error", "message": f"Client ID '{target_client_id}' not found."}), 404
        if group_name not in client_groups: return jsonify({"status": "error", "message": f"Group '{group_name}' not found."}), 404

        old_group = connected_clients[target_socket].get("group")
        if old_group and old_group != "all" and old_group in client_groups and target_socket in client_groups[old_group]:
            client_groups[old_group].remove(target_socket)

        if target_socket not in client_groups[group_name]: client_groups[group_name].append(target_socket)
        connected_clients[target_socket]["group"] = group_name
        
        if target_socket not in client_groups["all"]: client_groups["all"].append(target_socket)

        return jsonify({"status": "success", "message": f"Client '{connected_clients[target_socket]['username']}' (ID: {target_client_id}) assigned to group '{group_name}'."})
    
    return jsonify({"status": "error", "message": "Invalid action."}), 400

@app.route('/api/client/change_password', methods=['POST'])
def client_change_password():
    data = request.json
    username = data.get('username')
    client_id = data.get('client_id')
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not all([username, client_id, current_password, new_password]):
        return jsonify({"status": "error", "message": "Missing required fields."}), 400

    user_data = users_credentials.get(username)
    if not user_data:
        return jsonify({"status": "error", "message": "Username not found."}), 404

    # Kiểm tra xem client_id có khớp với username không (thêm một lớp bảo vệ)
    # và client có đang kết nối không.
    active_client_matches = False
    for sock, c_info in connected_clients.items():
        if c_info['username'] == username and c_info['client_id'] == client_id:
            active_client_matches = True
            break
    
    if not active_client_matches:
         return jsonify({"status": "error", "message": "Client not authenticated or ID mismatch."}), 403


    if not check_password_hash(user_data["hashed_password"], current_password):
        return jsonify({"status": "error", "message": "Incorrect current password."}), 403

    users_credentials[username]["hashed_password"] = generate_password_hash(new_password)
    # Ghi log thay đổi mật khẩu vào history của client đó
    if user_data.get("current_socket") and user_data["current_socket"] in connected_clients:
        connected_clients[user_data["current_socket"]]["history"].append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "command_type": "system_security",
            "content_sent": {"type": "Password Changed", "value": "User changed their password."}
        })
    print(f"Password changed for user: {username}")
    return jsonify({"status": "success", "message": "Password changed successfully."})


# --- Logic Socket Server (NCCP) ---
def send_to_client(client_socket, message_dict):
    try:
        message_json = json.dumps(message_dict)
        client_socket.sendall(message_json.encode('utf-8'))
        # Không log message ở đây nữa vì có thể chứa thông tin nhạy cảm (dù auth_success thì ok)
    except Exception as e:
        print(f"Error sending message to client: {e}")
        remove_client(client_socket)

def send_nccp_command(target_type, target_id, command_payload, current_content_summary):
    targets = []
    if target_type == "client":
        for sock, info in connected_clients.items():
            if info["client_id"] == target_id: # Target bằng client_id
                targets.append(sock)
                break
    elif target_type == "group":
        if target_id in client_groups: targets.extend(client_groups[target_id])
    
    if not targets:
        print(f"Warning: No targets found for {target_type} '{target_id}'")
        return

    for client_socket in targets:
        if client_socket in connected_clients:
            connected_clients[client_socket]["current_content"] = current_content_summary
            history_entry = {"timestamp": current_content_summary["timestamp"], "command_type": command_payload["type"], "content_sent": current_content_summary}
            connected_clients[client_socket]["history"].append(history_entry)
            max_history = 20
            if len(connected_clients[client_socket]["history"]) > max_history:
                connected_clients[client_socket]["history"] = connected_clients[client_socket]["history"][-max_history:]
        send_to_client(client_socket, command_payload)

def handle_client_connection(client_socket, address):
    print(f"Accepted connection from {address}, awaiting authentication...")
    auth_success = False
    client_username = None
    assigned_client_id = None

    try:
        auth_message_str = client_socket.recv(1024).decode('utf-8')
        if not auth_message_str:
            print(f"Client {address} disconnected before authentication.")
            client_socket.close()
            return

        auth_message = json.loads(auth_message_str)
        if auth_message.get("type") == "auth_request":
            payload = auth_message.get("payload", {})
            username = payload.get("username")
            password = payload.get("password")
            client_id_hint = payload.get("client_id_hint", f"client_{uuid.uuid4().hex[:6]}")

            user_data = users_credentials.get(username)
            if user_data and check_password_hash(user_data["hashed_password"], password):
                # Xác thực thành công
                auth_success = True
                client_username = username
                
                # Tạo/Lấy client_id: ưu tiên client_id_hint nếu nó chưa được dùng bởi user khác, hoặc tạo mới
                is_id_unique = True
                temp_assigned_id = client_id_hint
                for sock, existing_info in connected_clients.items():
                    if existing_info["client_id"] == temp_assigned_id and existing_info["username"] != client_username :
                        is_id_unique = False
                        break
                if not is_id_unique or any(c_info['client_id'] == temp_assigned_id and c_info['username'] != client_username for c_info in connected_clients.values()): # Double check
                    temp_assigned_id = f"{client_id_hint}_{uuid.uuid4().hex[:4]}"

                assigned_client_id = temp_assigned_id
                users_credentials[username]["current_socket"] = client_socket # Lưu socket hiện tại của user

                client_info = {
                    "username": client_username,
                    "client_id": assigned_client_id,
                    "address": address,
                    "group": users_credentials[username].get("group", "all"), # Lấy group từ user_credentials hoặc mặc định
                    "current_content": {"type": "N/A", "value": "Authenticated", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                    "history": [{
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "command_type": "system_auth",
                        "content_sent": {"type": "Authenticated", "value": f"User: {client_username}, ID: {assigned_client_id}"}
                    }]
                }
                connected_clients[client_socket] = client_info
                
                # Thêm vào group 'all' và group riêng của user (nếu có)
                if client_socket not in client_groups["all"]: client_groups["all"].append(client_socket)
                user_specific_group = client_info["group"]
                if user_specific_group not in client_groups: client_groups[user_specific_group] = []
                if client_socket not in client_groups[user_specific_group]: client_groups[user_specific_group].append(client_socket)


                ack_message = {"type": "auth_success", "payload": {"status": "authenticated", "client_id": assigned_client_id, "username": client_username, "group": client_info["group"]}}
                send_to_client(client_socket, ack_message)
                print(f"User '{client_username}' (ID: {assigned_client_id}) from {address} authenticated. Group: {client_info['group']}")
            else:
                ack_message = {"type": "auth_failure", "payload": {"status": "authentication_failed", "message": "Invalid username or password."}}
                send_to_client(client_socket, ack_message)
                print(f"Authentication failed for user '{username}' from {address if username else address}.")
        else:
            print(f"Invalid initial message from {address}. Expected auth_request.")
            send_to_client(client_socket, {"type": "error", "payload": {"message": "Invalid initial request"}})


        if not auth_success:
            client_socket.close()
            return

        # Giữ kết nối mở
        while True:
            data = client_socket.recv(1024)
            if not data:
                print(f"Client {client_username} (ID: {assigned_client_id}) disconnected.")
                if client_socket in connected_clients:
                     connected_clients[client_socket]["history"].append({
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "command_type": "system",
                        "content_sent": {"type": "Disconnected", "value": ""}
                    })
                     connected_clients[client_socket]["current_content"] = {"type": "Offline", "value": "N/A", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                break
            # Xử lý các loại message khác từ client nếu có

    except ConnectionResetError:
        print(f"Client {client_username or 'Unknown'} (ID: {assigned_client_id or 'N/A'}) forcibly closed connection.")
        if client_socket in connected_clients:
            connected_clients[client_socket]["history"].append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "command_type": "system", "content_sent": {"type": "Disconnected (Reset)", "value": ""}})
            connected_clients[client_socket]["current_content"] = {"type": "Offline", "value": "N/A", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except socket.error as e:
        print(f"Socket error with client {client_username or 'Unknown'} (ID: {assigned_client_id or 'N/A'}): {e}")
    except json.JSONDecodeError:
        print(f"Invalid JSON from client {client_username or 'Unknown'} (ID: {assigned_client_id or 'N/A'})")
    except Exception as e:
        print(f"An unexpected error occurred with client {client_username or 'Unknown'} (ID: {assigned_client_id or 'N/A'}): {e}")
    finally:
        if auth_success and client_username: # Chỉ remove nếu đã được thêm vào users_credentials
             if users_credentials.get(client_username): # Kiểm tra lại cho chắc
                 users_credentials[client_username]["current_socket"] = None
        remove_client(client_socket)


def remove_client(client_socket):
    client_info_data = connected_clients.pop(client_socket, None)
    if client_info_data:
        username_removed = client_info_data.get('username', 'Unknown')
        client_id_removed = client_info_data.get('client_id', 'N/A')
        print(f"Removing client: User '{username_removed}', ID '{client_id_removed}'")
        
        # Không cần cập nhật history/current_content ở đây nữa vì đã làm ở caller hoặc client_info_data đã bị pop

        for group_name, members in list(client_groups.items()):
            if client_socket in members:
                members.remove(client_socket)
    try:
        client_socket.close()
    except Exception as e:
        print(f"Error closing client socket: {e}")

def start_socket_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind((SERVER_HOST, SOCKET_PORT))
        server_socket.listen(5)
        print(f"Socket server listening on {SERVER_HOST}:{SOCKET_PORT}")
        while True:
            client_sock, address = server_socket.accept()
            client_thread = threading.Thread(target=handle_client_connection, args=(client_sock, address))
            client_thread.daemon = True
            client_thread.start()
    except OSError as e: print(f"!!! Socket server OS Error: {e}. Port {SOCKET_PORT} might be in use.")
    except Exception as e: print(f"!!! Socket server critical error: {e}")
    finally:
        server_socket.close()
        print("Socket server shut down.")

if __name__ == '__main__':
    # (Optional) Add a default user if not exists, for easier testing
    if "default_user" not in users_credentials:
        users_credentials["default_user"] = {"hashed_password": generate_password_hash("password"), "client_id_hint": "DefaultClient", "current_socket": None, "group": "all"}

    socket_thread = threading.Thread(target=start_socket_server)
    socket_thread.daemon = True
    socket_thread.start()

    print(f"Flask web server starting on http://{SERVER_HOST}:{FLASK_PORT}")
    app.run(host=SERVER_HOST, port=FLASK_PORT, debug=False)
