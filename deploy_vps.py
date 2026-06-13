import sys
import os
import getpass

try:
    import paramiko
except ImportError:
    print("Instalando biblioteca 'paramiko' para conexao SSH...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko

def run_ssh_commands(ip, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    print(f"\nConectando a root@{ip}...")
    try:
        ssh.connect(hostname=ip, username='root', password=password, timeout=15)
        print("Conectado com sucesso!")
    except Exception as e:
        print(f"Erro ao conectar na VPS: {e}")
        return False

    commands = [
        # 1. Update and install packages
        ("Atualizando pacotes e instalando dependencias do sistema...", 
         "sudo apt update && sudo apt install -y git python3 python3-pip python3-venv"),
        
        # 2. Clone or update repository
        ("Clonando/atualizando o repositorio...", 
         "if [ -d '/root/Robo-didi' ]; then cd /root/Robo-didi && git pull; else git clone https://github.com/rfnhaia-boop/Robo-didi.git /root/Robo-didi; fi"),
        
        # 3. Create virtual environment and install requirements
        ("Configurando ambiente virtual e dependencias Python...", 
         "cd /root/Robo-didi && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt"),
        
        # 4. Create systemd service
        ("Criando servico do sistema para rodar 24/7...", 
         """cat << 'EOF' > /etc/systemd/system/robo-didi.service
[Unit]
Description=Robo Didi Forex Server
After=network.target

[Service]
User=root
WorkingDirectory=/root/Robo-didi
ExecStart=/root/Robo-didi/.venv/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF
"""),
        
        # 5. Reload daemon and start service
        ("Iniciando e habilitando o servico...", 
         "sudo systemctl daemon-reload && sudo systemctl enable robo-didi && sudo systemctl restart robo-didi"),
        
        # 6. Open firewall port
        ("Liberando a porta 8000 no firewall...", 
         "sudo ufw allow 8000/tcp || true")
    ]

    for desc, cmd in commands:
        print(f"\n[+] {desc}")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        
        # Wait for command to complete
        exit_status = stdout.channel.recv_exit_status()
        
        out_str = stdout.read().decode('utf-8', errors='ignore')
        err_str = stderr.read().decode('utf-8', errors='ignore')
        
        if exit_status != 0:
            print(f"Erro (status {exit_status}):")
            print(err_str)
            ssh.close()
            return False
        else:
            if out_str.strip():
                print(out_str.strip())
            print("OK!")

    # Verify status
    print("\n[+] Verificando status do servico...")
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl status robo-didi")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
    ssh.close()
    print("\n=======================================================")
    print("DEPLOY CONCLUIDO COM SUCESSO!")
    print(f"Acesse o robo em: http://{ip}:8000")
    print("=======================================================")
    return True

if __name__ == '__main__':
    print("=== DEPLOY AUTOMATICO DO ROBO DIDI FOREX NA VPS ===")
    ip = input("IP da VPS: ").strip()
    password = getpass.getpass("Senha do root da VPS: ")
    
    if not ip or not password:
        print("IP e Senha sao obrigatorios.")
        sys.exit(1)
        
    run_ssh_commands(ip, password)
