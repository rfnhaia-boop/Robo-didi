#!/bin/bash
echo "=== INSTALANDO ROBO DIDI FOREX NA VPS ==="

# 1. Update and install dependencies
echo "[+] Atualizando pacotes..."
sudo apt update && sudo apt install -y git python3 python3-pip python3-venv ufw

# 2. Clone/update repository
echo "[+] Clonando repositorio..."
if [ -d "/root/Robo-didi" ]; then
    cd /root/Robo-didi
    git pull
else
    git clone https://github.com/rfnhaia-boop/Robo-didi.git /root/Robo-didi
    cd /root/Robo-didi
fi

# 3. Create virtual environment and install requirements
echo "[+] Configurando Python virtualenv..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. Create systemd service
echo "[+] Criando servico robo-didi.service..."
cat << 'EOF' > /etc/systemd/system/robo-didi.service
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

# 5. Start and enable systemd service
echo "[+] Iniciando servico..."
sudo systemctl daemon-reload
sudo systemctl enable robo-didi
sudo systemctl restart robo-didi

# 6. Configure firewall
echo "[+] Configurando regras de firewall local..."
sudo ufw allow 8000/tcp || true

echo "=== INSTALACAO CONCLUIDA COM SUCESSO! ==="
echo "Verifique o status do servico com: systemctl status robo-didi"
