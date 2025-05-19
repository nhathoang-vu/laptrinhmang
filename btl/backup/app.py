import flask
from flask import Flask, render_template, request, jsonify, send_from_directory
import socket
import threading
import json
import os
import uuid # Để tạo client_id tạm thời nếu client không gửi

# --- Cấu hình Server ---
SERVER_HOST = '26.162.100.45'  # Lắng nghe trên tất cả các interface
FLASK_PORT = 5000
SOCKET_PORT = 55555
CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content') # Đường dẫn tới thư mục content

# --- Quản lý Client và Nhóm (trong bộ nhớ) ---
connected_clients = {}  # {client_socket: {"id": "client_id", "address": addr, "group": "group_name"}}
client_groups = {"all": []} # {"group_name": [client_socket_1, client_socket_2]}

# --- Khởi tạo Flask App ---
app = Flask(__name__)
app.config['CONTENT_DIR'] = CONTENT_DIR

# --- Flask Routes (Giao diện Web và API) ---
@app.route('/')
def index():
    # Lấy danh sách file từ thư mục content
    image_files = []
    video_files = []
    text_files = []
    try:
        image_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'images')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'images', f))]
    except FileNotFoundError:
        pass # Bỏ qua nếu thư mục không tồn tại
    try:
        video_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'videos')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'videos', f))]
    except FileNotFoundError:
        pass
    try:
        text_files = [f for f in os.listdir(os.path.join(app.config['CONTENT_DIR'], 'texts')) if os.path.isfile(os.path.join(app.config['CONTENT_DIR'], 'texts', f))]
    except FileNotFoundError:
        pass

    # Chuẩn bị dữ liệu client cho template
    clients_info = []
    for sock, info in connected_clients.items():
        clients_info.append({
            "id": info["id"],
            "address": f"{info['address'][0]}:{info['address'][1]}",
            "group": info.get("group", "N/A")
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
    target_type = data.get('target_type') # "client" or "group"
    target_id = data.get('target_id')     # client_id or group_name
    content_type = data.get('content_type')
    content_value = data.get('content_value') # text content or filename

    command_payload = {}

    if content_type == 'text_direct':
        command_payload = {
            "type": "display_content",
            "payload": {
                "content_type": "text",
                "data": content_value
            }
        }
    elif content_type == 'text_file':
        filepath = os.path.join('texts', content_value)
        try:
            with open(os.path.join(app.config['CONTENT_DIR'], filepath), 'r', encoding='utf-8') as f:
                text_data = f.read()
            command_payload = {
                "type": "display_content",
                "payload": {
                    "content_type": "text",
                    "data": text_data
                }
            }
        except Exception as e:
            return jsonify({"status": "error", "message": f"Error reading text file: {str(e)}"}), 500
    elif content_type in ['image', 'video']:
        # Client sẽ fetch file này từ server qua HTTP
        # Đường dẫn URL mà client sẽ dùng để fetch
        file_url = f"/content/{content_type}s/{content_value}"
        command_payload = {
            "type": "display_content",
            "payload": {
                "content_type": content_type,
                "url": file_url, # URL để client fetch
                "filename": content_value
            }
        }
    elif content_type == 'clear':
        command_payload = {
            "type": "clear_content",
            "payload": {}
        }
    else:
        return jsonify({"status": "error", "message": "Invalid content type"}), 400

    if not command_payload:
         return jsonify({"status": "error", "message": "Could not create command payload"}), 500

    send_nccp_command(target_type, target_id, command_payload)
    return jsonify({"status": "success", "message": "Command sent"})

@app.route('/api/manage_group', methods=['POST'])
def manage_group_route():
    data = request.json
    action = data.get('action') # "create", "assign"
    group_name = data.get('group_name')
    client_id = data.get('client_id') # Chỉ dùng cho "assign"

    if action == "create":
        if group_name and group_name not in client_groups:
            client_groups[group_name] = []
            return jsonify({"status": "success", "message": f"Group '{group_name}' created."})
        return jsonify({"status": "error", "message": "Invalid group name or group exists."}), 400
    
    elif action == "assign":
        target_socket = None
        for sock, info in connected_clients.items():
            if info["id"] == client_id:
                target_socket = sock
                break
        
        if not target_socket:
            return jsonify({"status": "error", "message": f"Client '{client_id}' not found."}), 404
        if group_name not in client_groups:
            return jsonify({"status": "error", "message": f"Group '{group_name}' not found."}), 404

        # Xóa client khỏi nhóm cũ (nếu có và không phải nhóm 'all')
        old_group = connected_clients[target_socket].get("group")
        if old_group and old_group != "all" and old_group in client_groups and target_socket in client_groups[old_group]:
            client_groups[old_group].remove(target_socket)

        # Gán vào nhóm mới
        if target_socket not in client_groups[group_name]:
             client_groups[group_name].append(target_socket)
        connected_clients[target_socket]["group"] = group_name
        
        # Cũng luôn thêm vào nhóm 'all' (nếu chưa có)
        if target_socket not in client_groups["all"]:
            client_groups["all"].append(target_socket)

        return jsonify({"status": "success", "message": f"Client '{client_id}' assigned to group '{group_name}'."})
    
    return jsonify({"status": "error", "message": "Invalid action."}), 400


# --- Logic Socket Server (NCCP) ---
def send_to_client(client_socket, message_dict):
    try:
        message_json = json.dumps(message_dict)
        client_socket.sendall(message_json.encode('utf-8'))
        print(f"Sent to {connected_clients.get(client_socket, {}).get('id', 'Unknown')}: {message_json}")
    except Exception as e:
        print(f"Error sending message to client: {e}")
        remove_client(client_socket)

def send_nccp_command(target_type, target_id, command_payload):
    targets = []
    if target_type == "client":
        for sock, info in connected_clients.items():
            if info["id"] == target_id:
                targets.append(sock)
                break
    elif target_type == "group":
        if target_id in client_groups:
            targets.extend(client_groups[target_id])
    
    if not targets:
        print(f"Warning: No targets found for {target_type} '{target_id}'")
        return

    for client_socket in targets:
        send_to_client(client_socket, command_payload)

def handle_client_connection(client_socket, address):
    print(f"Accepted connection from {address}")
    temp_id = f"client_{uuid.uuid4().hex[:6]}" # ID tạm thời
    client_info = {"id": temp_id, "address": address, "group": "all"}
    connected_clients[client_socket] = client_info
    if client_socket not in client_groups["all"]: # Đảm bảo client luôn trong nhóm "all"
        client_groups["all"].append(client_socket)

    try:
        # Nhận client_hello
        hello_message_str = client_socket.recv(1024).decode('utf-8')
        if hello_message_str:
            hello_message = json.loads(hello_message_str)
            if hello_message.get("type") == "client_hello":
                client_id_from_msg = hello_message.get("payload", {}).get("client_id", temp_id)
                # Kiểm tra ID duy nhất
                is_unique = True
                for sock, info_val in connected_clients.items():
                    if sock != client_socket and info_val["id"] == client_id_from_msg:
                        is_unique = False
                        break
                if not is_unique:
                    client_id_from_msg = f"{client_id_from_msg}_{uuid.uuid4().hex[:3]}" # Thêm hậu tố nếu trùng
                
                connected_clients[client_socket]["id"] = client_id_from_msg
                client_group_from_msg = hello_message.get("payload", {}).get("group")
                if client_group_from_msg:
                    if client_group_from_msg not in client_groups:
                         client_groups[client_group_from_msg] = [] # Tạo nhóm nếu chưa có
                    if client_socket not in client_groups[client_group_from_msg]:
                        client_groups[client_group_from_msg].append(client_socket)
                    connected_clients[client_socket]["group"] = client_group_from_msg


                print(f"Client {client_id_from_msg} from {address} registered. Group: {connected_clients[client_socket]['group']}")
                ack_message = {"type": "server_ack", "payload": {"status": "connected", "client_id": client_id_from_msg}}
                send_to_client(client_socket, ack_message)
            else:
                print("Did not receive valid client_hello.")
        else:
            print("Client closed connection before hello.")
            remove_client(client_socket)
            return


        # Giữ kết nối mở để nhận thêm (nếu cần) hoặc chỉ chờ đóng
        while True:
            data = client_socket.recv(1024) # Lắng nghe để phát hiện ngắt kết nối
            if not data:
                print(f"Client {connected_clients[client_socket]['id']} disconnected.")
                break
            # Xử lý các loại message khác từ client nếu có (ví dụ: heartbeat)
            # message = json.loads(data.decode('utf-8'))
            # print(f"Received from {connected_clients[client_socket]['id']}: {message}")

    except ConnectionResetError:
        print(f"Client {connected_clients.get(client_socket, {}).get('id', 'Unknown')} forcibly closed connection.")
    except socket.error as e:
        print(f"Socket error with client {connected_clients.get(client_socket, {}).get('id', 'Unknown')}: {e}")
    except json.JSONDecodeError:
        print(f"Invalid JSON from client {connected_clients.get(client_socket, {}).get('id', 'Unknown')}")
    finally:
        remove_client(client_socket)


def remove_client(client_socket):
    client_info = connected_clients.pop(client_socket, None)
    if client_info:
        print(f"Removing client: {client_info['id']}")
        # Xóa client khỏi tất cả các nhóm
        for group_name, members in list(client_groups.items()): # list() để tránh lỗi thay đổi dict khi duyệt
            if client_socket in members:
                members.remove(client_socket)
                # Nếu nhóm rỗng và không phải là "all", có thể xóa nhóm (tùy chọn)
                # if not members and group_name != "all":
                #     del client_groups[group_name]
    try:
        client_socket.close()
    except Exception as e:
        print(f"Error closing client socket: {e}")


def start_socket_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Cho phép tái sử dụng địa chỉ
    try:
        server_socket.bind((SERVER_HOST, SOCKET_PORT))
        server_socket.listen(5) # Lắng nghe tối đa 5 kết nối chờ
        print(f"Socket server listening on {SERVER_HOST}:{SOCKET_PORT}")

        while True:
            client_socket, address = server_socket.accept()
            client_thread = threading.Thread(target=handle_client_connection, args=(client_socket, address))
            client_thread.daemon = True # Để thread tự thoát khi main thread thoát
            client_thread.start()
    except OSError as e:
        print(f"!!! Socket server OS Error: {e}. Port {SOCKET_PORT} might be in use.")
    except Exception as e:
        print(f"!!! Socket server critical error: {e}")
    finally:
        server_socket.close()
        print("Socket server shut down.")


if __name__ == '__main__':
    # Chạy Socket server trong một thread riêng
    socket_thread = threading.Thread(target=start_socket_server)
    socket_thread.daemon = True
    socket_thread.start()

    # Chạy Flask server (chạy ở main thread)
    print(f"Flask web server starting on http://{SERVER_HOST}:{FLASK_PORT}")
    app.run(host=SERVER_HOST, port=FLASK_PORT, debug=False) # debug=False cho production/test LAN