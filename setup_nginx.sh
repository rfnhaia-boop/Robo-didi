#!/bin/bash
# Configura Nginx como proxy reverso + HTTPS (Let's Encrypt) para o Robo Didi.
# Resultado: https://robodidi.newflowsys.cloud (sem porta, com cadeado).
set -e

DOMINIO="robodidi.newflowsys.cloud"
EMAIL="new.company.sys@gmail.com"

echo "[+] Instalando Nginx e Certbot..."
apt update
apt install -y nginx certbot python3-certbot-nginx

echo "[+] Criando configuracao do site (com suporte a WebSocket)..."
cat > /etc/nginx/sites-available/robodidi << EOF
server {
    listen 80;
    server_name $DOMINIO;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }
}
EOF

ln -sf /etc/nginx/sites-available/robodidi /etc/nginx/sites-enabled/robodidi
rm -f /etc/nginx/sites-enabled/default

echo "[+] Testando e recarregando Nginx..."
nginx -t
systemctl reload nginx

echo "[+] Liberando portas 80 e 443 no firewall..."
ufw allow 80/tcp || true
ufw allow 443/tcp || true

echo "[+] Emitindo certificado SSL (Let's Encrypt)..."
certbot --nginx -d $DOMINIO --non-interactive --agree-tos -m $EMAIL --redirect

echo "=== PRONTO! Acesse https://$DOMINIO ==="
